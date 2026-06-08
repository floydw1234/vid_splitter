from __future__ import annotations

import argparse
import random
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FillerSelection:
    start: float
    end: float
    length: float


def _normalize_intervals(
    intervals: list[tuple[float, float]] | None,
    duration: float,
) -> list[tuple[float, float]]:
    if not intervals:
        return []

    clamped: list[tuple[float, float]] = []
    for start, end in intervals:
        start = max(0.0, min(float(start), duration))
        end = max(0.0, min(float(end), duration))
        if end <= start:
            continue
        clamped.append((start, end))

    if not clamped:
        return []

    clamped.sort()
    merged = [clamped[0]]
    for start, end in clamped[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _allowed_intervals(
    duration: float,
    avoided_intervals: list[tuple[float, float]] | None,
) -> list[tuple[float, float]]:
    blocked = _normalize_intervals(avoided_intervals, duration)
    if not blocked:
        return [(0.0, duration)]

    allowed: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in blocked:
        if cursor < start:
            allowed.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < duration:
        allowed.append((cursor, duration))
    return allowed


def pick_filler_window(
    duration: float,
    desired_length: float,
    seed: int,
    avoided_intervals: list[tuple[float, float]] | None = None,
) -> FillerSelection:
    duration = float(duration)
    desired_length = float(desired_length)
    if duration <= 0:
        raise ValueError("duration must be positive")
    if desired_length <= 0:
        raise ValueError("desired_length must be positive")

    clip_length = min(duration, desired_length)
    candidates = []
    for start, end in _allowed_intervals(duration, avoided_intervals):
        if end - start >= clip_length:
            candidates.append((start, end))

    if not candidates:
        raise ValueError("No filler window fits outside avoided intervals")

    exact_fits = [start for start, end in candidates if abs((end - start) - clip_length) < 1e-9]
    flexible = [(start, end - clip_length) for start, end in candidates if end - start > clip_length]

    if flexible:
        total_span = sum(end - start for start, end in flexible)
        rng = random.Random(seed)
        offset = rng.random() * total_span
        for start, latest_start in flexible:
            span = latest_start - start
            if offset <= span:
                chosen_start = start + offset
                break
            offset -= span
        else:
            chosen_start = flexible[-1][1]
    else:
        chosen_start = exact_fits[seed % len(exact_fits)]

    chosen_end = min(duration, chosen_start + clip_length)
    return FillerSelection(
        start=round(chosen_start, 6),
        end=round(chosen_end, 6),
        length=round(chosen_end - chosen_start, 6),
    )


def build_ffmpeg_extract_command(
    source_path: str | Path,
    output_path: str | Path,
    selection: FillerSelection,
) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-ss",
        f"{selection.start:.3f}",
        "-i",
        str(source_path),
        "-t",
        f"{selection.length:.3f}",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


def probe_media_duration(source_path: str | Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(source_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def extract_filler_clip(
    source_path: str | Path,
    output_path: str | Path,
    duration: float,
    desired_length: float,
    seed: int,
    avoided_intervals: list[tuple[float, float]] | None = None,
) -> FillerSelection:
    source = Path(source_path)
    output = Path(output_path)
    if not source.exists():
        raise FileNotFoundError(f"Source video not found: {source}")

    selection = pick_filler_window(
        duration=duration,
        desired_length=desired_length,
        seed=seed,
        avoided_intervals=avoided_intervals,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        build_ffmpeg_extract_command(source, output, selection),
        check=True,
        capture_output=True,
    )
    return selection


def _parse_avoid_interval(value: str) -> tuple[float, float]:
    parts = value.split(":", 1)
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"Invalid interval '{value}', expected start:end")
    try:
        start = float(parts[0])
        end = float(parts[1])
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid interval '{value}', expected numeric start:end"
        ) from exc
    if end <= start:
        raise argparse.ArgumentTypeError(f"Invalid interval '{value}', end must be greater than start")
    return (start, end)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Select and extract a deterministic filler clip.")
    parser.add_argument("source", help="Source video path")
    parser.add_argument("--duration", type=float, help="Source duration in seconds")
    parser.add_argument("--seed", type=int, required=True, help="Deterministic seed")
    parser.add_argument("--output", required=True, help="Output MP4 path")
    parser.add_argument(
        "--clip-length",
        "--desired-length",
        dest="desired_length",
        type=float,
        default=5.0,
        help="Desired filler clip length in seconds",
    )
    parser.add_argument(
        "--avoid",
        action="append",
        type=_parse_avoid_interval,
        default=[],
        help="Forbidden interval in start:end seconds format. Repeatable.",
    )
    args = parser.parse_args(argv)

    duration = args.duration if args.duration is not None else probe_media_duration(args.source)
    selection = extract_filler_clip(
        source_path=Path(args.source),
        output_path=Path(args.output),
        duration=duration,
        desired_length=args.desired_length,
        seed=args.seed,
        avoided_intervals=args.avoid,
    )
    print(
        f"Selected filler clip start={selection.start:.3f}s end={selection.end:.3f}s "
        f"length={selection.length:.3f}s output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
