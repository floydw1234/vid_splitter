"""Background topic detection jobs."""

from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from editor.ffmpeg_util import probe_video
from editor.project import (
    Project,
    load_project,
    save_project,
)
from editor.segments import merge_flagged_frames, merged_to_segments

DEFAULT_TOPICS = ["asian people", "black people", "lgbtq"]
DEFAULT_THRESHOLD = 0.5
DEFAULT_INTERVAL = 2.0

_scanner_cache: Any = None
_scanner_lock = __import__("threading").Lock()


def use_fake_detector() -> bool:
    return os.environ.get("VID_EDITOR_FAKE_DETECTOR", "").strip() in {
        "1",
        "true",
        "yes",
    }


def fake_scores_for_time(t: float, topics: list[str]) -> dict[str, float]:
    """Deterministic pseudo-scores for dev/tests without GPU."""
    out: dict[str, float] = {}
    for i, topic in enumerate(topics):
        phase = t * 0.7 + i * 1.3
        out[topic] = 0.35 + 0.4 * (0.5 + 0.5 * math.sin(phase))
    return out


def _get_scanner():
    global _scanner_cache
    if use_fake_detector():
        return None
    with _scanner_lock:
        if _scanner_cache is None:
            from analyzer.clef_topics import ClefFlashTopicScanner

            _scanner_cache = ClefFlashTopicScanner()
        return _scanner_cache


def extract_frames(
    video_path: Path,
    dest_dir: Path,
    interval: float,
    duration: float,
) -> list[tuple[float, Path]]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    pattern = dest_dir / "frame_%05d.jpg"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vf",
            f"fps=1/{max(interval, 0.1):.6f},scale=-2:720",
            "-q:v",
            "3",
            str(pattern),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    frames: list[tuple[float, Path]] = []
    for path in sorted(dest_dir.glob("frame_*.jpg")):
        idx = int(path.stem.split("_")[1]) - 1
        t = idx * interval
        if t >= duration:
            break
        frames.append((t, path))
    return frames


def score_frame_image(scanner, image_path: Path, topics: list[str], t: float) -> dict[str, float]:
    if use_fake_detector():
        return fake_scores_for_time(t, topics)
    from PIL import Image

    img = Image.open(image_path)
    scores = scanner.scores_for_image(img)
    return {k: float(v) for k, v in scores.items()}


def run_detection(
    data_dir: Path,
    project_id: str,
    *,
    topics: list[str] | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    interval: float = DEFAULT_INTERVAL,
    on_progress: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    topics = topics or list(DEFAULT_TOPICS)
    project = load_project(data_dir, project_id)
    pdir = data_dir / "projects" / project_id
    video = pdir / project.source_video

    def prog(p: float, msg: str) -> None:
        if on_progress:
            on_progress(p, msg)

    prog(0.02, "extracting frames")
    with tempfile.TemporaryDirectory(prefix="editor_frames_") as tmp:
        tmp_path = Path(tmp)
        frames_paths = extract_frames(video, tmp_path, interval, project.duration)
        prog(0.15, f"scored 0/{len(frames_paths)} frames")

        scanner = _get_scanner()
        if scanner is not None:
            scanner.active_labels = frozenset(topics)
            scanner.threshold = threshold
        frame_records: list[dict[str, Any]] = []
        for i, (t, fpath) in enumerate(frames_paths):
            scores = score_frame_image(scanner, fpath, topics, t)
            frame_records.append({"t": t, "scores": scores})
            if frames_paths:
                prog(0.15 + 0.75 * (i + 1) / len(frames_paths), f"scoring frame {i + 1}")

    scores_path = pdir / "frame_scores.json"
    scores_path.write_text(json.dumps(frame_records, indent=2) + "\n", encoding="utf-8")

    merge_pad = max(0.5, interval / 2.0)
    merged = merge_flagged_frames(
        frame_records,
        interval,
        threshold,
        pad=merge_pad,
        duration=project.duration,
    )
    suggested = merged_to_segments(merged)

    kept = [
        s
        for s in project.segments
        if s.source == "manual" or s.status == "accepted"
    ]
    project.segments = kept + suggested
    save_project(data_dir, project)
    prog(1.0, "done")
    return {"suggested_count": len(suggested), "frame_count": len(frame_records)}
