from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest


DEFAULT_SHORTS_DIR = Path("/mnt/hdds/Videos/shorts")
VIDEO_EXTENSIONS = (".mp4", ".mkv", ".mov", ".m4v", ".avi", ".webm")


@dataclass(frozen=True)
class SmokeConfig:
    jellyfin_base_url: str | None
    jellyfin_api_key: str | None
    jellyfin_username: str | None
    jellyfin_password: str | None
    jellyfin_shorts_dir: str | None


@dataclass(frozen=True)
class VideoSelectionResult:
    path: Path | None
    skip_reason: str | None


@dataclass(frozen=True)
class PlaybackReadinessResult:
    selected_profile: str
    selected_source_id: str
    playable_media_source_count: int


@dataclass(frozen=True)
class SmokeWorkflowResult:
    selected_video: Path
    generated_bvf: Path
    discovery: dict[str, list]
    playback_readiness: PlaybackReadinessResult


class JellyfinApiError(RuntimeError):
    pass


def load_smoke_config() -> SmokeConfig:
    return SmokeConfig(
        jellyfin_base_url=os.environ.get("JELLYFIN_BASE_URL"),
        jellyfin_api_key=os.environ.get("JELLYFIN_API_KEY"),
        jellyfin_username=os.environ.get("JELLYFIN_USERNAME"),
        jellyfin_password=os.environ.get("JELLYFIN_PASSWORD"),
        jellyfin_shorts_dir=os.environ.get("JELLYFIN_SHORTS_DIR"),
    )


def require_prerequisites_or_skip(config: SmokeConfig) -> None:
    missing: list[str] = []
    if not config.jellyfin_base_url:
        missing.append("JELLYFIN_BASE_URL")

    has_api_key = bool(config.jellyfin_api_key)
    has_user_pass = bool(config.jellyfin_username and config.jellyfin_password)
    if not has_api_key and not has_user_pass:
        missing.append("JELLYFIN_API_KEY or JELLYFIN_USERNAME/JELLYFIN_PASSWORD")

    if missing:
        pytest.skip("Missing Jellyfin smoke configuration: " + ", ".join(missing))


def build_api_key_headers(api_key: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Authorization": f'MediaBrowser Token="{api_key}"',
    }


def build_json_headers(extra_headers: dict[str, str] | None = None) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    return headers


def parse_json_response(response) -> dict:
    raw_payload = response.read()
    if not raw_payload:
        return {}

    payload = json.loads(raw_payload.decode("utf-8"))
    if not isinstance(payload, dict):
        raise JellyfinApiError("Expected JSON object response from Jellyfin API")
    return payload


def normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def parse_smart_branch_discovery(payload: dict) -> dict[str, list]:
    smart_branch_sources = []
    profiles: list[str] = []

    for source in payload.get("MediaSources", []):
        name = str(source.get("Name", ""))
        if not name.startswith("Smart Branch (") or not name.endswith(")"):
            continue

        profile = name[len("Smart Branch (") : -1].strip()
        smart_branch_sources.append(source)
        if profile:
            profiles.append(profile)

    return {
        "sources": smart_branch_sources,
        "profiles": sorted(set(profiles)),
    }


def select_smart_branch_source(discovery: dict[str, list], requested_profile: str | None = None) -> tuple[str, dict]:
    sources = discovery.get("sources", [])
    if not sources:
        raise JellyfinApiError("No Smart Branch sources available for playback readiness check")

    if requested_profile:
        expected_name = f"Smart Branch ({requested_profile})"
        for source in sources:
            if source.get("Name") == expected_name:
                return requested_profile, source
        raise JellyfinApiError(f"Requested Smart Branch profile not found: {requested_profile}")

    first_source = sources[0]
    name = str(first_source.get("Name", ""))
    if name.startswith("Smart Branch (") and name.endswith(")"):
        return name[len("Smart Branch (") : -1].strip(), first_source
    raise JellyfinApiError("Unable to determine default Smart Branch profile source")


class JellyfinClient:
    def __init__(self, config: SmokeConfig):
        self._config = config
        self._base_url = normalize_base_url(config.jellyfin_base_url or "")

    def get_public_system_info(self) -> dict:
        return self._request_json("GET", "/System/Info/Public")

    def authenticate(self) -> str:
        if self._config.jellyfin_api_key:
            return self._config.jellyfin_api_key
        return self.authenticate_with_password()

    def authenticate_with_password(self) -> str:
        payload = self._request_json(
            "POST",
            "/Users/AuthenticateByName",
            body={
                "Username": self._config.jellyfin_username,
                "Pw": self._config.jellyfin_password,
            },
        )
        token = payload.get("AccessToken")
        if not token:
            raise JellyfinApiError("Jellyfin login response did not include AccessToken")
        return token

    def get_authenticated_headers(self) -> dict[str, str]:
        return build_api_key_headers(self.authenticate())

    def find_movie_item(self, movie_path: Path, search_terms: list[str]) -> dict | None:
        query = {"searchTerm": search_terms[0]} if search_terms else {"searchTerm": movie_path.stem}
        payload = self._request_json("GET", "/Items", query=query)
        items = payload.get("Items", [])

        # Prefer the first-class .bvf library item, then fall back to legacy mp4 path.
        candidate_paths = []
        if movie_path.suffix.lower() == ".bvf":
            candidate_paths.append(str(movie_path))
        else:
            candidate_paths.append(str(get_bvf_path(movie_path)))
            candidate_paths.append(str(movie_path))

        for candidate in candidate_paths:
            exact_matches = [item for item in items if item.get("Path") == candidate]
            if exact_matches:
                return exact_matches[0]

        if len(items) == 0:
            return None
        if len(items) == 1:
            return items[0]
        raise JellyfinApiError(f"Ambiguous Jellyfin item lookup for {movie_path}")

    def refresh_item(self, item_id: str) -> dict:
        return self._request_json("POST", f"/Items/{item_id}/Refresh")

    def refresh_library(self) -> dict:
        return self._request_json("POST", "/Library/Refresh")

    def issue_refresh(self, item_id: str | None) -> dict:
        if item_id:
            return self.refresh_item(item_id)
        return self.refresh_library()

    def get_smart_branch_sources(self, item_id: str) -> list[dict]:
        payload = self._request_json("GET", f"/Items/{item_id}/PlaybackInfo")
        discovery = parse_smart_branch_discovery(payload)
        return discovery["sources"]

    def wait_for_smart_branch_sources(
        self,
        item_id: str,
        timeout_seconds: float,
        poll_interval_seconds: float,
    ) -> list[dict]:
        deadline = time.monotonic() + timeout_seconds
        while True:
            sources = self.get_smart_branch_sources(item_id)
            if sources:
                return sources
            if time.monotonic() >= deadline:
                raise JellyfinApiError(
                    f"Timed out waiting for Smart Branch sources for item {item_id}"
                )
            time.sleep(poll_interval_seconds)

    def verify_smart_branch_discovery(
        self,
        item_id: str,
        timeout_seconds: float,
        poll_interval_seconds: float,
    ) -> dict[str, list]:
        self.issue_refresh(item_id)
        sources = self.wait_for_smart_branch_sources(
            item_id=item_id,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        discovery = parse_smart_branch_discovery({"MediaSources": sources})
        if not discovery["sources"]:
            raise JellyfinApiError(
                "No Smart Branch sources discovered after refresh. Check Jellyfin plugin logs "
                "and verify the .bvf library item is present and readable."
            )
        return discovery

    def check_playback_readiness(
        self,
        item_id: str,
        discovery: dict[str, list],
        requested_profile: str | None = None,
    ) -> PlaybackReadinessResult:
        selected_profile, selected_source = select_smart_branch_source(
            discovery,
            requested_profile=requested_profile,
        )
        payload = self._request_json(
            "POST",
            f"/Items/{item_id}/PlaybackInfo",
            body={"MediaSourceId": selected_source["Id"]},
        )
        error_code = payload.get("ErrorCode")
        if error_code:
            raise JellyfinApiError(f"Playback not ready for profile {selected_profile}: {error_code}")

        media_sources = payload.get("MediaSources", [])
        if not media_sources:
            raise JellyfinApiError(
                f"No playable media sources returned for profile {selected_profile}"
            )

        return PlaybackReadinessResult(
            selected_profile=selected_profile,
            selected_source_id=str(selected_source["Id"]),
            playable_media_source_count=len(media_sources),
        )

    def _request_json(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        headers: dict[str, str] | None = None,
        query: dict[str, str] | None = None,
    ) -> dict:
        request = self._build_request(method, path, body=body, headers=headers, query=query)
        try:
            with urlopen(request) as response:
                return parse_json_response(response)
        except HTTPError as exc:
            raise JellyfinApiError(
                f"Jellyfin API request failed with status {exc.code}: {exc.reason}"
            ) from exc

    def _build_request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        headers: dict[str, str] | None = None,
        query: dict[str, str] | None = None,
    ) -> Request:
        request_headers = build_json_headers(headers)
        payload = None
        if body is not None:
            request_headers["Content-Type"] = "application/json"
            payload = json.dumps(body).encode("utf-8")

        url = self._base_url + path
        if query:
            url += "?" + urlencode(query)

        return Request(
            url=url,
            data=payload,
            headers=request_headers,
            method=method,
        )


def list_video_candidates(base_dir: Path) -> list[Path]:
    return sorted(
        path for path in base_dir.iterdir() if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    )


def get_bvf_path(movie_path: Path) -> Path:
    return movie_path.with_suffix(".bvf")


def build_analyzer_command(movie_path: Path, output_dir: Path) -> list[str]:
    return [
        sys.executable,
        "analyzer/analyze.py",
        str(movie_path),
        "--demo-branch",
        "--output-dir",
        str(output_dir),
    ]


def run_analyzer(command: list[str]) -> Path:
    subprocess.run(command, check=True, capture_output=True, text=True)
    movie_path = Path(command[2])
    output_dir = Path(command[command.index("--output-dir") + 1])
    return output_dir / f"{movie_path.stem}.bvf"


def probe_bvf(bvf_path: Path) -> None:
    subprocess.run(
        [sys.executable, "tools/bvf_probe.py", str(bvf_path)],
        check=True,
        capture_output=True,
        text=True,
    )


def select_video_candidate(default_shorts_dir: Path = DEFAULT_SHORTS_DIR) -> VideoSelectionResult:
    config = load_smoke_config()
    base_dir = Path(config.jellyfin_shorts_dir) if config.jellyfin_shorts_dir else default_shorts_dir

    if not base_dir.exists():
        return VideoSelectionResult(
            path=None,
            skip_reason=(
                f"Set JELLYFIN_SHORTS_DIR or ensure the default shorts directory exists: {base_dir}"
            ),
        )

    candidates = list_video_candidates(base_dir)
    if not candidates:
        return VideoSelectionResult(
            path=None,
            skip_reason=f"No supported video files found in {base_dir}",
        )

    return VideoSelectionResult(path=candidates[0], skip_reason=None)


def run_smoke() -> VideoSelectionResult:
    config = load_smoke_config()
    require_prerequisites_or_skip(config)
    return select_video_candidate()


def perform_smoke_workflow(
    requested_profile: str | None = None,
    timeout_seconds: float = 30.0,
    poll_interval_seconds: float = 1.0,
) -> SmokeWorkflowResult:
    selection = run_smoke()
    if selection.path is None:
        raise pytest.skip.Exception(selection.skip_reason or "No video candidate selected")

    video_path = selection.path
    analyzer_command = build_analyzer_command(video_path, video_path.parent)
    bvf_path = run_analyzer(analyzer_command)
    probe_bvf(bvf_path)

    client = JellyfinClient(load_smoke_config())
    # After plugin install, .bvf is the library item (sibling mp4 is ignored when present).
    item = client.find_movie_item(bvf_path, search_terms=[video_path.stem])
    if item is None:
        item = client.find_movie_item(video_path, search_terms=[video_path.stem])
    if item is None:
        raise JellyfinApiError(f"Jellyfin item not found for BVF/video: {bvf_path}")

    discovery = client.verify_smart_branch_discovery(
        item_id=str(item["Id"]),
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    playback_readiness = client.check_playback_readiness(
        item_id=str(item["Id"]),
        discovery=discovery,
        requested_profile=requested_profile,
    )
    return SmokeWorkflowResult(
        selected_video=video_path,
        generated_bvf=bvf_path,
        discovery=discovery,
        playback_readiness=playback_readiness,
    )


def format_smoke_summary(result: SmokeWorkflowResult) -> str:
    profiles = ", ".join(result.discovery["profiles"])
    readiness = result.playback_readiness
    return "\n".join(
        [
            f"Selected video: {result.selected_video}",
            f"Generated BVF: {result.generated_bvf}",
            f"Discovered profiles: {profiles}",
            (
                "Playback ready: yes "
                f"(profile={readiness.selected_profile}, media_sources={readiness.playable_media_source_count})"
            ),
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Jellyfin Smart Branch smoke workflow")
    parser.add_argument("--profile", help="Optional Smart Branch profile to verify")
    parser.add_argument("--dry-run", action="store_true", help="Run the smoke workflow without extra operator actions")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=1.0)
    args = parser.parse_args(argv or [])

    try:
        result = perform_smoke_workflow(
            requested_profile=args.profile,
            timeout_seconds=args.timeout_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
        )
    except pytest.skip.Exception as exc:
        print(f"SKIPPED: {exc}")
        return 2
    except (JellyfinApiError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}")
        return 1

    print(format_smoke_summary(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
