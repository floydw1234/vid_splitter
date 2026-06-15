from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

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

    candidates = sorted(
        path for path in base_dir.iterdir() if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    )
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
