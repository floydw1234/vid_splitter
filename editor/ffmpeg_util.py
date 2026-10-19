"""ffmpeg/ffprobe helpers (no FastAPI)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def probe_video(path: Path) -> dict[str, float | int | bool]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate,duration",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    stream = (payload.get("streams") or [{}])[0]
    fmt = payload.get("format") or {}
    duration = float(stream.get("duration") or fmt.get("duration") or 0.0)
    fps_raw = str(stream.get("r_frame_rate") or "24/1")
    if "/" in fps_raw:
        num, den = fps_raw.split("/", 1)
        fps = float(num) / float(den or 1)
    else:
        fps = float(fps_raw)
    audio = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "width": int(stream.get("width") or 1280),
        "height": int(stream.get("height") or 720),
        "duration": duration,
        "fps": fps,
        "has_audio": bool(audio.stdout.strip()),
    }


def parse_fps(rate: str) -> float:
    if "/" in rate:
        num, den = rate.split("/", 1)
        return float(num) / float(den or 1)
    return float(rate)
