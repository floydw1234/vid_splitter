"""Pure segment merging from per-frame topic scores."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from editor.project import Segment, validate_segment


@dataclass(frozen=True)
class MergedSegment:
    start: float
    end: float
    topic: str
    score: float


def merge_flagged_frames(
    frames: list[dict[str, Any]],
    interval: float,
    threshold: float,
    *,
    pad: float = 0.5,
    max_gap: float | None = None,
    duration: float | None = None,
) -> list[MergedSegment]:
    """Merge consecutive flagged samples per topic into padded intervals."""
    if interval <= 0:
        raise ValueError("interval must be positive")
    gap_limit = max_gap if max_gap is not None else interval * 1.5
    by_topic: dict[str, list[tuple[float, float]]] = {}

    for frame in frames:
        t = float(frame["t"])
        scores: dict[str, float] = frame.get("scores") or {}
        for topic, score in scores.items():
            if float(score) >= threshold:
                by_topic.setdefault(topic, []).append((t, float(score)))

    merged: list[MergedSegment] = []
    for topic, hits in by_topic.items():
        if not hits:
            continue
        hits.sort(key=lambda x: x[0])
        run_start = hits[0][0]
        run_end = hits[0][0]
        run_score = hits[0][1]
        for t, score in hits[1:]:
            if t - run_end <= gap_limit:
                run_end = t
                run_score = max(run_score, score)
            else:
                merged.append(_padded(topic, run_start, run_end, run_score, pad, duration))
                run_start = t
                run_end = t
                run_score = score
        merged.append(_padded(topic, run_start, run_end, run_score, pad, duration))

    merged.sort(key=lambda s: (s.start, s.topic))
    return merged


def _padded(
    topic: str,
    start: float,
    end: float,
    score: float,
    pad: float,
    duration: float | None,
) -> MergedSegment:
    s = max(0.0, start - pad)
    e = end + pad
    if duration is not None:
        e = min(duration, e)
    if e <= s:
        e = min(s + 0.01, duration if duration is not None else s + 0.01)
    return MergedSegment(start=s, end=e, topic=topic, score=score)


def normalize_segments(segments: list[Segment], duration: float) -> list[Segment]:
    """Sort segments and validate; raises ProjectValidationError on failure."""
    ordered = sorted(segments, key=lambda s: (s.start, s.end, s.id))
    for seg in ordered:
        validate_segment(seg, duration)
    return ordered


def merged_to_segments(merged: list[MergedSegment]) -> list[Segment]:
    from editor.project import new_segment_id

    out: list[Segment] = []
    for m in merged:
        out.append(
            Segment(
                id=new_segment_id(),
                start=m.start,
                end=m.end,
                topic=m.topic,
                score=m.score,
                source="suggested",
                status="suggested",
                action="none",
            )
        )
    return out
