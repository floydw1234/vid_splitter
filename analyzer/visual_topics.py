"""Attach clef-flash visual topic hits to analyzer segment buckets."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# Transcript classifiers skip these; appearance is scored on video frames only.
APPEARANCE_LABELS = {
    "black people": "black",
    "asian people": "asian",
    "asian person": "asian",
}


class VisualTopicScanner(Protocol):
    def topics_for_path(self, path: str | Path) -> list[str]: ...


def _frame_in_segment(timestamp: float, start: float, end: float, *, is_last: bool) -> bool:
    """Include bucket boundaries so a sample at 35s tags both 30–35 and 35–40."""
    _ = is_last
    return start <= timestamp <= end


def attach_visual_topics(
    segments: list[dict],
    frames: list[dict[str, Any]],
    scanner: VisualTopicScanner,
) -> list[dict]:
    """Union visual labels from frames that fall inside each segment."""
    if not frames:
        return segments
    cache: dict[str, list[str]] = {}
    labeled: list[tuple[float, list[str]]] = []
    for frame in frames:
        path = frame.get("frame_path")
        timestamp = frame.get("time")
        if not path or timestamp is None or not Path(path).is_file():
            continue
        try:
            if path not in cache:
                cache[path] = scanner.topics_for_path(path)
            topics = cache[path]
        except Exception as exc:
            logger.warning("Visual topic scan failed for %s: %s", path, exc)
            continue
        if topics:
            labeled.append((float(timestamp), topics))

    last_index = len(segments) - 1
    updated = []
    for index, seg in enumerate(segments):
        start = float(seg.get("start_time", 0.0))
        end = float(seg.get("end_time", 0.0))
        topics = set(seg.get("topics") or [])
        is_last = index == last_index
        for timestamp, frame_topics in labeled:
            if _frame_in_segment(timestamp, start, end, is_last=is_last):
                topics.update(frame_topics)
        row = dict(seg)
        row["topics"] = sorted(topics)
        updated.append(row)
    return updated
