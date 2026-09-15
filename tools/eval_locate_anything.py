#!/usr/bin/env python3
"""Score LocateAnything vs CLIP vs hand labels on extracted Ms Rachel stills."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from analyzer.locate_anything import DEFAULT_PROMPTS, LocateAnythingScanner
from analyzer.topic_classifier import taxonomy_with_extras
from analyzer.visual_topics import VISUAL_PROMPTS, ClipTopicScanner

# Clip-relative seconds on the 2-minute extracts.
CLIP31 = {
    5: set(),
    20: set(),
    40: {"black people"},
    50: {"black people"},
    70: {"black people"},
    85: {"black people"},
    95: {"black people"},
    102: {"asian people"},
    106: {"black people"},
    109: {"asian people"},
    118: {"black people"},
}
CLIP35 = {
    5: set(),
    20: {"lgbtq"},
    30: {"lgbtq"},
    50: {"lgbtq"},
    61: {"black people"},
    70: {"lgbtq"},
    80: {"lgbtq"},
    100: {"lgbtq"},
    115: set(),
}


def _frame_time(path: Path) -> int:
    return int(path.stem.lstrip("t"))


def _run_dir(scanner, frames: list[Path], truth: dict[int, set[str]], keys: set[str]) -> list[dict]:
    rows = []
    for path in sorted(frames, key=_frame_time):
        t = _frame_time(path)
        pred = set(scanner.topics_for_path(path)) & keys
        gt = truth.get(t, set())
        rows.append(
            {
                "frame": path.name,
                "t": t,
                "gt": sorted(gt),
                "pred": sorted(pred),
                "fp": sorted(pred - gt),
                "fn": sorted(gt - pred),
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames31", default="/tmp/la_eval/clip31")
    parser.add_argument("--frames35", default="/tmp/la_eval/clip35")
    parser.add_argument("--clip", action="store_true")
    parser.add_argument("--locate", action="store_true")
    args = parser.parse_args()
    keys = {"black people", "asian people", "lgbtq"}
    if args.locate:
        locate = LocateAnythingScanner()
        print("LocateAnything 31-33m")
        print(json.dumps(_run_dir(locate, list(Path(args.frames31).glob("*.jpg")), CLIP31, keys), indent=2))
        print("LocateAnything 35-37m")
        print(json.dumps(_run_dir(locate, list(Path(args.frames35).glob("*.jpg")), CLIP35, keys), indent=2))
    if args.clip:
        clip = ClipTopicScanner(
            device="cuda",
            taxonomy=taxonomy_with_extras(list(DEFAULT_PROMPTS), dict(VISUAL_PROMPTS)),
        )
        print("CLIP 31-33m")
        print(json.dumps(_run_dir(clip, list(Path(args.frames31).glob("*.jpg")), CLIP31, keys), indent=2))
        print("CLIP 35-37m")
        print(json.dumps(_run_dir(clip, list(Path(args.frames35).glob("*.jpg")), CLIP35, keys), indent=2))


if __name__ == "__main__":
    main()
