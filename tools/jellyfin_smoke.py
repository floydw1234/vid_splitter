from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError
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
    payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise JellyfinApiError("Expected JSON object response from Jellyfin API")
    return payload


def normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


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

    def _request_json(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict:
        request = self._build_request(method, path, body=body, headers=headers)
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
    ) -> Request:
        request_headers = build_json_headers(headers)
        payload = None
        if body is not None:
            request_headers["Content-Type"] = "application/json"
            payload = json.dumps(body).encode("utf-8")

        return Request(
            url=self._base_url + path,
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
