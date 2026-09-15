#!/usr/bin/env python3
"""Run CLIP vs LocateAnything on hand-labeled Ms Rachel stills and write a PDF report."""

from __future__ import annotations

import argparse
import json
import shutil
import textwrap
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from analyzer.locate_anything import (
    DEFAULT_PROMPTS,
    LocateAnythingScanner,
    merge_visual_labels,
)
from analyzer.topic_classifier import taxonomy_with_extras
from analyzer.visual_topics import VISUAL_PROMPTS, ClipTopicScanner
from tools.eval_locate_anything import CLIP31, CLIP35

BAKEOFF_KEYS = {"black people", "asian people", "lgbtq"}
PROMPTS_SUBSET = {k: v for k, v in DEFAULT_PROMPTS.items() if k in BAKEOFF_KEYS}


def _frame_time(path: Path) -> int:
    return int(path.stem.lstrip("t"))


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _scale_box(box: list[float], src_size: tuple[int, int], dst_size: tuple[int, int]) -> tuple[int, int, int, int]:
    sw, sh = src_size
    dw, dh = dst_size
    x1, y1, x2, y2 = box
    return (
        int(x1 * dw / sw),
        int(y1 * dh / sh),
        int(x2 * dw / sw),
        int(y2 * dh / sh),
    )


def _draw_boxes(image: Image.Image, detections: list[dict], title: str) -> Image.Image:
    out = image.copy().convert("RGB")
    draw = ImageDraw.Draw(out)
    font = _load_font(18)
    draw.rectangle((0, 0, out.width, 28), fill=(20, 20, 20))
    draw.text((8, 5), title, fill=(255, 255, 255), font=font)
    for det in detections:
        box = det.get("box")
        if not box or len(box) != 4:
            continue
        xy = _scale_box(box, (1920, 1080), out.size)
        label = det.get("label") or det.get("topic") or "?"
        if det.get("phrase"):
            label = f"{label} ({det['phrase']})"
        draw.rectangle(xy, outline=(255, 80, 0), width=3)
        draw.text((xy[0] + 4, max(30, xy[1] + 4)), str(label)[:40], fill=(255, 220, 0), font=font)
    return out


def _text_panel(width: int, lines: list[str]) -> Image.Image:
    font = _load_font(16)
    line_h = 22
    height = 32 + line_h * len(lines)
    panel = Image.new("RGB", (width, height), (245, 245, 245))
    draw = ImageDraw.Draw(panel)
    y = 10
    for line in lines:
        draw.text((10, y), line, fill=(20, 20, 20), font=font)
        y += line_h
    return panel


def _compose_page(
    frame_path: Path,
    clip_name: str,
    truth: set[str],
    clip_pred: set[str],
    la_combined: set[str],
    la_per_topic: set[str],
    combined: set[str],
    la_boxes: list[dict],
) -> Image.Image:
    base = Image.open(frame_path).convert("RGB")
    thumb_w = 640
    thumb_h = int(base.height * thumb_w / base.width)
    thumb = base.resize((thumb_w, thumb_h))
    boxed = _draw_boxes(thumb, la_boxes, "LocateAnything per-tag boxes")

    def fmt(name: str, pred: set[str]) -> str:
        fp = sorted(pred - truth)
        fn = sorted(truth - pred)
        ok = not fp and not fn
        mark = "OK" if ok else ("FP" if fp else "FN" if fn else "mix")
        return f"{name}: {sorted(pred) or '-'}  [{mark}]"

    lines = [
        f"{clip_name}  t={_frame_time(frame_path)}s",
        f"Ground truth: {sorted(truth) or '-'}",
        fmt("CLIP (+ R-CNN appearance)", clip_pred),
        fmt("LocateAnything combined prompt", la_combined),
        fmt("LocateAnything per-tag", la_per_topic),
        fmt("Combined (CLIP + per-tag LA)", combined),
    ]
    if sorted(truth - clip_pred):
        lines.append(f"CLIP missed: {sorted(truth - clip_pred)}")
    if sorted(clip_pred - truth):
        lines.append(f"CLIP false alarm: {sorted(clip_pred - truth)}")
    if sorted(truth - la_per_topic):
        lines.append(f"Locate missed: {sorted(truth - la_per_topic)}")
    if sorted(la_per_topic - truth):
        lines.append(f"Locate false alarm: {sorted(la_per_topic - truth)}")

    text = _text_panel(thumb_w * 2, lines)
    page = Image.new("RGB", (thumb_w * 2, thumb_h + text.height + 10), (255, 255, 255))
    page.paste(thumb, (0, 0))
    page.paste(boxed, (thumb_w, 0))
    page.paste(text, (0, thumb_h + 10))
    return page


def _score_rows(truth: dict[int, set[str]], frames: list[Path], pred_fn) -> list[dict]:
    rows = []
    for path in sorted(frames, key=_frame_time):
        t = _frame_time(path)
        gt = truth.get(t, set())
        pred = set(pred_fn(path)) & BAKEOFF_KEYS
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


def _summarize(name: str, rows: list[dict]) -> dict:
    tp = fp = fn = 0
    for row in rows:
        gt = set(row["gt"])
        pred = set(row["pred"])
        tp += len(gt & pred)
        fp += len(pred - gt)
        fn += len(gt - pred)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    return {
        "system": name,
        "frames": len(rows),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(prec, 3),
        "recall": round(rec, 3),
    }


def run_bakeoff(
    *,
    frames31: Path,
    frames35: Path,
    output_dir: Path,
    keys: set[str] | None = None,
) -> Path:
    keys = keys or BAKEOFF_KEYS
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = output_dir / f"ms_rachel_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    for clip, src in (("clip31", frames31), ("clip35", frames35)):
        dst = run_dir / clip / "frames"
        dst.mkdir(parents=True, exist_ok=True)
        for path in sorted(src.glob("*.jpg")):
            shutil.copy2(path, dst / path.name)

    clip_scanner = ClipTopicScanner(
        device="cuda",
        taxonomy=taxonomy_with_extras(list(keys), dict(VISUAL_PROMPTS)),
    )
    locate = LocateAnythingScanner(prompts=PROMPTS_SUBSET)

    datasets = [
        ("clip31", run_dir / "clip31" / "frames", CLIP31),
        ("clip35", run_dir / "clip35" / "frames", CLIP35),
    ]

    all_pages: list[Image.Image] = []
    la_per_cache: dict[str, list[str]] = {}
    la_box_cache: dict[str, list[dict]] = {}

    def la_per_topics(path: Path) -> list[str]:
        key = str(path)
        if key not in la_per_cache:
            la_per_cache[key] = locate.topics_for_path(path, per_topic=True)
        return la_per_cache[key]

    def la_per_boxes(path: Path) -> list[dict]:
        key = str(path)
        if key not in la_box_cache:
            la_box_cache[key] = locate.detections_for_path(path, per_topic=True)
        return la_box_cache[key]

    summary: dict = {
        "created_utc": stamp,
        "note": (
            "Hand-labeled stills from ms_rachel_31m-33m and ms_rachel_35m-37m. "
            "LocateAnything 'combined prompt' uses one </c>-joined query (known weak). "
            "LocateAnything 'per-tag' runs one detect query per label."
        ),
        "systems": [
            "CLIP (+ Faster R-CNN person crops for appearance tags)",
            "LocateAnything combined prompt",
            "LocateAnything per-tag",
            "Combined = CLIP with per-tag LocateAnything veto/confirm on owned tags",
        ],
        "clips": {},
    }

    title = _text_panel(
        1200,
        [
            "Vision bakeoff — Ms Rachel 31-33m and 35-37m",
            "Ground truth: hand labels on sampled stills (appearance + pride-flag lgbtq).",
            "Orange boxes = LocateAnything per-tag detections.",
            "See summary.json for precision/recall per system.",
        ],
    )
    all_pages.append(title)

    for clip_name, frame_dir, truth in datasets:
        frames = list(frame_dir.glob("*.jpg"))
        clip_rows = _score_rows(truth, frames, lambda p: clip_scanner.topics_for_path(p))
        la_combined_rows = _score_rows(
            truth, frames, lambda p: locate.topics_for_path_combined_prompt(p)
        )
        la_per_rows = _score_rows(truth, frames, la_per_topics)
        combined_rows = _score_rows(
            truth,
            frames,
            lambda p: merge_visual_labels(
                list(clip_scanner.topics_for_path(p)),
                la_per_topics(p),
                locate_keys=set(locate.prompts),
            ),
        )

        summary["clips"][clip_name] = {
            "frames": len(frames),
            "results": {
                "clip": clip_rows,
                "locate_combined": la_combined_rows,
                "locate_per_tag": la_per_rows,
                "combined": combined_rows,
            },
            "scores": [
                _summarize("clip", clip_rows),
                _summarize("locate_combined", la_combined_rows),
                _summarize("locate_per_tag", la_per_rows),
                _summarize("combined", combined_rows),
            ],
        }

        for path in frames:
            t = _frame_time(path)
            gt = truth.get(t, set())
            clip_pred = set(clip_scanner.topics_for_path(path)) & keys
            la_combined = set(locate.topics_for_path_combined_prompt(path)) & keys
            la_per = set(la_per_topics(path)) & keys
            combined = set(
                merge_visual_labels(
                    list(clip_scanner.topics_for_path(path)),
                    la_per_topics(path),
                    locate_keys=set(locate.prompts),
                )
            ) & keys
            boxes = [d for d in la_per_boxes(path) if d.get("topic") in keys]
            interesting = gt != clip_pred or gt != la_per or clip_pred != la_per or la_combined != la_per
            if interesting or gt:
                page = _compose_page(
                    path,
                    clip_name,
                    gt,
                    clip_pred,
                    la_combined,
                    la_per,
                    combined,
                    boxes,
                )
                page_path = run_dir / clip_name / "pages" / f"{path.stem}.png"
                page_path.parent.mkdir(parents=True, exist_ok=True)
                page.save(page_path)
                all_pages.append(page)

    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    readme = textwrap.dedent(
        f"""\
        # Vision bakeoff

        Generated: {stamp} UTC

        ## What this is

        The "first bakeoff" was an ad-hoc terminal run on {frames31} stills right after
        the LocateAnything GGUF finished downloading. Those results were not saved to disk.

        This directory is the persisted rerun with annotated pages and a PDF.

        ## Layout

        - `summary.json` — per-frame hits/misses and precision/recall
        - `clip31/frames`, `clip35/frames` — copied stills
        - `clip31/pages`, `clip35/pages` — composite PNGs (original + LA boxes + scores)
        - `vision_bakeoff.pdf` — all pages

        ## Systems

        1. **CLIP** — softmax over text prompts; appearance tags use Faster R-CNN person crops
        2. **LocateAnything combined** — all labels in one `</c>` prompt (weak; misses most boxes)
        3. **LocateAnything per-tag** — one detect query per label (~13s/frame/tag on GB10)
        4. **Combined** — CLIP plus per-tag LocateAnything confirm/veto on owned tags
        """
    )
    (run_dir / "README.md").write_text(readme)

    pdf_path = run_dir / "vision_bakeoff.pdf"
    rgb_pages = [p.convert("RGB") for p in all_pages]
    rgb_pages[0].save(pdf_path, save_all=True, append_images=rgb_pages[1:])
    return run_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="CLIP vs LocateAnything bakeoff report")
    parser.add_argument("--frames31", type=Path, default=Path("/tmp/la_eval/clip31"))
    parser.add_argument("--frames35", type=Path, default=Path("/tmp/la_eval/clip35"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output/vision_bakeoff"),
    )
    args = parser.parse_args()
    run_dir = run_bakeoff(
        frames31=args.frames31,
        frames35=args.frames35,
        output_dir=args.output_dir,
    )
    print(run_dir)
    print(run_dir / "vision_bakeoff.pdf")


if __name__ == "__main__":
    main()
