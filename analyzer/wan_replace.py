"""Coalesce analyzer buckets into Wan Animate replacement runs."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_WAN_PROMPT = (
    "Keep the same people, faces, motion, camera angle, and setting."
)
DEFAULT_WAN_TAGS = ()
DEFAULT_WAN_PROFILES = ("child", "teen_m", "teen_f")
DEFAULT_WAN_MAX_RUN_S = 12.0
DEFAULT_WAN_OVERLAP_S = 0.5

REMOVAL_PHRASES = {
    "nudity": "nudity and exposed skin; dress everyone in modest clothing",
    "religion_christianity": (
        "Christian symbols including crosses, crucifixes, churches, Jesus imagery, and Bible scenes"
    ),
    "religion_general": "religious worship, prayer, and sacred symbols",
    "politics": "political campaigning, slogans, and partisan signs",
    "feminism": "feminist slogans and protest signage",
    "profanity": "visible profanity and explicit language on screen",
    "drugs": "drug use and drug paraphernalia",
    "alcohol": "drinking and alcoholic beverages",
    "smoking": "smoking and vaping",
    "lgbtq": "pride flags and LGBTQ protest signage",
    "gore": "graphic gore and severe injury",
    "violence": "visible violence and assault",
    "violence_domestic": "domestic assault",
    "war_military": "warfare and combat",
    "language": "spoken or written profanity",
}


@dataclass(frozen=True)
class WanRun:
    start: float
    end: float
    segment_ids: tuple[str, ...]
    tags: tuple[str, ...]

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def parse_csv(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _segment_labels(seg: dict) -> set[str]:
    return set(seg.get("tags") or []) | set(seg.get("topics") or [])


def prompt_for_tags(tags: tuple[str, ...] | list[str], extra: str | None = None) -> str:
    """Build a removal prompt from every label on the run, then append extras."""
    phrases = []
    seen: set[str] = set()
    for label in tags:
        key = str(label).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        phrases.append(REMOVAL_PHRASES.get(key, key.replace("_", " ")))
    body = "; ".join(phrases) if phrases else "any flagged mature or restricted content"
    prompt = f"{DEFAULT_WAN_PROMPT} Remove or replace: {body}."
    extra_text = (extra or "").strip()
    if extra_text:
        prompt = f"{prompt} {extra_text}"
    return prompt


def coalesce_tagged_runs(
    segments: list[dict],
    *,
    tags: tuple[str, ...] | set[str],
    segment_ids: tuple[str, ...] | set[str] | None = None,
    gap: float = 0.051,
) -> list[WanRun]:
    wanted = set(tags)
    allowed = set(segment_ids) if segment_ids else None
    runs: list[WanRun] = []
    current: WanRun | None = None

    for seg in segments:
        if seg.get("is_filler"):
            continue
        hit = wanted.intersection(_segment_labels(seg))
        if allowed is not None and seg.get("id") not in allowed:
            hit = set()
        if not hit:
            if current is not None:
                runs.append(current)
                current = None
            continue
        start = float(seg["start_time"])
        end = float(seg["end_time"])
        seg_id = str(seg["id"])
        ordered_tags = tuple(sorted(hit))
        if current is not None and start <= current.end + gap:
            current = WanRun(
                start=current.start,
                end=end,
                segment_ids=current.segment_ids + (seg_id,),
                tags=tuple(sorted(set(current.tags) | set(ordered_tags))),
            )
        else:
            if current is not None:
                runs.append(current)
            current = WanRun(
                start=start,
                end=end,
                segment_ids=(seg_id,),
                tags=ordered_tags,
            )
    if current is not None:
        runs.append(current)
    return runs


def chunk_span(
    start: float,
    end: float,
    max_run: float,
    overlap: float,
) -> list[tuple[float, float]]:
    start = float(start)
    end = float(end)
    if end <= start:
        return []
    if max_run <= 0 or (end - start) <= max_run + 1e-9:
        return [(round(start, 3), round(end, 3))]
    overlap = min(max(0.0, float(overlap)), max_run / 2.0)
    chunks: list[tuple[float, float]] = []
    cursor = start
    while cursor < end - 1e-9:
        chunk_end = min(end, cursor + max_run)
        chunks.append((round(cursor, 3), round(chunk_end, 3)))
        if chunk_end >= end - 1e-9:
            break
        nxt = chunk_end - overlap
        if nxt <= cursor + 1e-9:
            nxt = chunk_end
        cursor = nxt
    return chunks


def merge_run_segments(segments: list[dict], runs: list[WanRun]) -> list[dict]:
    """Collapse each run's buckets into the first segment so one filler can swap it."""
    if not runs:
        return [dict(seg) for seg in segments]
    first_ids = {run.segment_ids[0]: run for run in runs if run.segment_ids}
    drop_ids = {sid for run in runs for sid in run.segment_ids[1:]}
    merged: list[dict] = []
    for seg in segments:
        seg_id = seg.get("id")
        if seg_id in drop_ids:
            continue
        updated = dict(seg)
        run = first_ids.get(seg_id)
        if run is not None:
            updated["start_time"] = round(run.start, 2)
            updated["end_time"] = round(run.end, 2)
            updated["tags"] = sorted(set(updated.get("tags") or []) | set(run.tags))
            updated["topics"] = sorted(set(updated.get("topics") or []) | set(run.tags))
            updated["risk"] = "mature"
            updated["action"] = "swap"
        merged.append(updated)
    return merged
