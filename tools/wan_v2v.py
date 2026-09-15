#!/usr/bin/env python3
"""Rewrite a short clip with Wan 2.2 Animate fp8 through local ComfyUI.

Uses the KJ packed Animate weights already under $COMFYUI_ROOT/models and the
API graph exported as character_replace_api.json. Source video supplies pose
and motion; --image is the appearance reference (first frame if omitted).

    python3 tools/wan_v2v.py clip.mp4 -p "the same people, fully clothed" -o out.mp4

ComfyUI must already be running (default http://127.0.0.1:8188).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_COMFY_ROOT = Path(
    os.environ.get("COMFYUI_ROOT", "/home/william/Documents/codingProj/ComfyUI")
)
DEFAULT_COMFY_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")
DEFAULT_WORKFLOW = Path(
    os.environ.get(
        "WAN_WORKFLOW",
        str(DEFAULT_COMFY_ROOT / "character_replace_api.json"),
    )
)
DEFAULT_NEGATIVE = (
    "色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，"
    "整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，"
    "画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，"
    "静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走"
)
logger = logging.getLogger(__name__)
ANIMATE_FPS = 16
REQUIRED_WEIGHTS = (
    "models/diffusion_models/Wan2_2-Animate-14B_fp8_e4m3fn_scaled_KJ.safetensors",
    "models/vae/Wan2_1_VAE_bf16.safetensors",
    "models/text_encoders/umt5-xxl-enc-bf16.safetensors",
    "models/clip_vision/CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors",
    "models/loras/wan/style/WanAnimate_relight_lora_fp16.safetensors",
    "models/loras/wan/lightx2v_T2V_14B_cfg_step_distill_v2_lora_rank256_bf16.safetensors",
    "models/detection/yolov10m.onnx",
    "models/detection/vitpose_h_wholebody_model.onnx",
)


@dataclass(frozen=True)
class AnimateJob:
    prompt: str
    negative: str
    video_name: str
    image_name: str
    prefix: str
    width: int
    height: int
    seed: int
    steps: int | None
    cfg: float | None
    shift: float | None


def wan_frame_count(n: int) -> int:
    """Wan length is 4k+1."""
    n = max(1, int(n))
    return ((n - 1) // 4) * 4 + 1


def fit_size(width: int, height: int, max_side: int = 1280) -> tuple[int, int]:
    width, height = int(width), int(height)
    if width <= 0 or height <= 0:
        return 1280, 720
    if width >= height:
        new_w = min(max_side, width)
        new_h = int(height * new_w / width)
    else:
        new_h = min(max_side, height)
        new_w = int(width * new_h / height)
    new_w = max(16, (new_w // 16) * 16)
    new_h = max(16, (new_h // 16) * 16)
    return new_w, new_h


def missing_checkpoint_files(comfy_root: Path) -> list[str]:
    missing = []
    for rel in REQUIRED_WEIGHTS:
        if not (comfy_root / rel).is_file():
            missing.append(rel)
    return missing


def probe_video(path: Path) -> dict[str, float | int | bool]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,duration:format=duration",
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
        "duration": float(stream.get("duration") or fmt.get("duration") or 0.0),
        "has_audio": bool(audio.stdout.strip()),
    }


def extract_frame(video: Path, dest: Path, start: float) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{max(0.0, start):.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            str(dest),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def trim_video(video: Path, dest: Path, start: float, duration: float) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{max(0.0, start):.3f}",
            "-i",
            str(video),
            "-t",
            f"{max(0.1, duration):.3f}",
            "-an",
            "-vf",
            f"fps={ANIMATE_FPS}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(dest),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def mux_audio(video: Path, audio_src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video),
            "-i",
            str(audio_src),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            str(dest),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _is_grid_combine(node: dict[str, Any]) -> bool:
    ins = node.get("inputs") or {}
    name = str(ins.get("filename_prefix", "")).lower()
    return "grid" in name or ins.get("frame_rate") == 1


def apply_job(workflow: dict[str, Any], job: AnimateJob) -> dict[str, Any]:
    """Patch the exported Animate graph with this job's inputs."""
    combines: list[dict[str, Any]] = []
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type")
        inputs = node.setdefault("inputs", {})
        title = str((node.get("_meta") or {}).get("title") or "")
        if class_type == "LoadImage":
            inputs["image"] = job.image_name
        elif class_type == "VHS_LoadVideo":
            inputs["video"] = job.video_name
            inputs["force_rate"] = int(ANIMATE_FPS)
        elif class_type == "WanVideoTextEncodeCached":
            inputs["positive_prompt"] = job.prompt
            inputs["negative_prompt"] = job.negative
        elif class_type == "WanVideoSampler":
            inputs["seed"] = job.seed
            if job.steps is not None:
                inputs["steps"] = job.steps
            if job.cfg is not None:
                inputs["cfg"] = job.cfg
            if job.shift is not None:
                inputs["shift"] = job.shift
        elif class_type == "INTConstant" and title == "Width":
            inputs["value"] = job.width
        elif class_type == "INTConstant" and title == "Height":
            inputs["value"] = job.height
        elif class_type == "VHS_VideoCombine":
            combines.append(node)

    target = None
    ranked: list[tuple[int, dict[str, Any]]] = []
    for node in combines:
        inputs = node.get("inputs") or {}
        if not isinstance(inputs.get("images"), list) or _is_grid_combine(node):
            continue
        score = 0
        if inputs.get("save_output"):
            score += 10
        if not isinstance(inputs.get("audio"), list):
            score += 1
        ranked.append((score, node))
    if ranked:
        target = max(ranked, key=lambda item: item[0])[1]
    for node in combines:
        inputs = node.setdefault("inputs", {})
        if node is target:
            inputs["filename_prefix"] = job.prefix
            inputs["save_output"] = True
            inputs.pop("audio", None)
            inputs["trim_to_audio"] = False
        elif not _is_grid_combine(node):
            inputs["save_output"] = False
        else:
            inputs["save_output"] = False
    return workflow


def _http_json(url: str, payload: dict[str, Any] | None = None) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="GET" if data is None else "POST")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise ConnectionError(f"ComfyUI not reachable at {url}: {exc}") from exc


def queue_prompt(comfy_url: str, workflow: dict[str, Any]) -> str:
    resp = _http_json(f"{comfy_url.rstrip('/')}/prompt", {"prompt": workflow})
    prompt_id = resp.get("prompt_id")
    if not prompt_id:
        raise RuntimeError(f"ComfyUI did not queue the prompt: {resp}")
    return str(prompt_id)


def wait_for_prompt(comfy_url: str, prompt_id: str, poll: float = 2.0) -> dict[str, Any]:
    url = f"{comfy_url.rstrip('/')}/history/{prompt_id}"
    started = time.monotonic()
    last_report = started
    logger.info("Waiting on Comfy prompt %s", prompt_id)
    while True:
        hist = _http_json(url)
        item = hist.get(prompt_id) if isinstance(hist, dict) else None
        if isinstance(item, dict):
            status = item.get("status") or {}
            if status.get("status_str") == "error" or status.get("error"):
                raise RuntimeError(f"ComfyUI job failed: {status}")
            if status.get("completed"):
                elapsed = time.monotonic() - started
                logger.info("Comfy prompt %s finished in %.0fs", prompt_id, elapsed)
                return item
        now = time.monotonic()
        if now - last_report >= 30:
            logger.info(
                "Comfy prompt %s still running (%.0fs)",
                prompt_id,
                now - started,
            )
            last_report = now
        time.sleep(poll)


def find_output_video(comfy_root: Path, prefix: str) -> Path | None:
    out_dir = comfy_root / "output"
    if not out_dir.is_dir():
        return None
    needle = prefix.replace("\\", "/").split("/")[-1]
    cands = [
        p
        for p in out_dir.rglob("*")
        if p.is_file()
        and p.suffix.lower() in {".mp4", ".webm"}
        and (prefix in str(p) or needle in p.name)
    ]
    if not cands:
        return None
    return max(cands, key=lambda p: p.stat().st_mtime)


def copy_into_input(comfy_root: Path, src: Path, dest_name: str) -> Path:
    dest = comfy_root / "input" / dest_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)
    return dest


def generate_animate(
    prompt: str,
    output: Path,
    video: Path,
    *,
    image: Path | None = None,
    start: float = 0.0,
    duration: float | None = None,
    frames: int | None = None,
    seed: int = 42,
    steps: int | None = None,
    cfg: float | None = None,
    shift: float | None = None,
    negative: str = DEFAULT_NEGATIVE,
    keep_audio: bool = True,
    comfy_root: Path = DEFAULT_COMFY_ROOT,
    comfy_url: str = DEFAULT_COMFY_URL,
    workflow_path: Path = DEFAULT_WORKFLOW,
    dry_run: bool = False,
) -> Path:
    comfy_root = comfy_root.resolve()
    workflow_path = workflow_path.resolve()
    output = output.resolve()
    video = video.resolve()
    if not video.is_file():
        raise FileNotFoundError(video)
    if not workflow_path.is_file():
        raise FileNotFoundError(
            f"Animate API workflow not found: {workflow_path}. "
            "Export it from ComfyUI as character_replace_api.json."
        )
    missing = missing_checkpoint_files(comfy_root)
    if missing:
        raise FileNotFoundError(
            "Wan 2.2 Animate fp8 checkpoint is incomplete under "
            f"{comfy_root}. Missing: {', '.join(missing)}"
        )

    info = probe_video(video)
    width, height = fit_size(int(info["width"]), int(info["height"]))
    if duration is None:
        frame_n = wan_frame_count(frames if frames is not None else 81)
        duration = frame_n / float(ANIMATE_FPS)
    remaining = float(info["duration"]) - start if float(info["duration"]) > 0 else duration
    if remaining > 0:
        duration = min(duration, remaining)

    work = Path(os.environ.get("TMPDIR", "/tmp")) / f"wan_v2v_{os.getpid()}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        clip = work / "clip.mp4"
        trim_video(video, clip, start, duration)
        if image is not None:
            ref = image.resolve()
            if not ref.is_file():
                raise FileNotFoundError(ref)
        else:
            ref = work / "ref.png"
            extract_frame(clip, ref, 0.0)

        stem = output.stem.replace(" ", "_") or "wan_animate"
        prefix = f"wan_v2v/{stem}_{os.getpid()}"
        video_name = f"{stem}_{os.getpid()}_clip.mp4"
        image_name = f"{stem}_{os.getpid()}_ref{ref.suffix or '.png'}"
        job = AnimateJob(
            prompt=prompt,
            negative=negative,
            video_name=video_name,
            image_name=image_name,
            prefix=prefix,
            width=width,
            height=height,
            seed=seed,
            steps=steps,
            cfg=cfg,
            shift=shift,
        )
        workflow = apply_job(json.loads(workflow_path.read_text()), job)
        if dry_run:
            print(json.dumps({"prefix": prefix, "job": job.__dict__, "url": comfy_url}))
            return output

        copy_into_input(comfy_root, clip, video_name)
        copy_into_input(comfy_root, ref, image_name)
        output.parent.mkdir(parents=True, exist_ok=True)
        logger.info(
            "Queueing Wan Animate %.1fs @ %.1fs (%dx%d, %s fps in)",
            duration,
            start,
            width,
            height,
            ANIMATE_FPS,
        )
        prompt_id = queue_prompt(comfy_url, workflow)
        wait_for_prompt(comfy_url, prompt_id)
        token = f"{stem}_{os.getpid()}"
        generated = find_output_video(comfy_root, token)
        if generated is None:
            generated = find_output_video(comfy_root, prefix.replace("/", os.sep))
        if generated is None:
            raise RuntimeError(f"ComfyUI did not write an mp4 for prefix {prefix}")
        if keep_audio and info["has_audio"]:
            mux_audio(generated, video, output)
        else:
            shutil.copy2(generated, output)
        return output
    finally:
        shutil.rmtree(work, ignore_errors=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Video-to-video clip rewrite with Wan 2.2 Animate fp8 via ComfyUI."
    )
    parser.add_argument("video", type=Path, help="Source clip (pose / motion)")
    parser.add_argument(
        "-p",
        "--prompt",
        required=True,
        help="Positive prompt describing the rewritten appearance",
    )
    parser.add_argument("-o", "--out", type=Path, required=True, help="Output mp4")
    parser.add_argument(
        "--image",
        type=Path,
        default=None,
        help="Appearance reference; default is the first frame of the trimmed clip",
    )
    parser.add_argument("--start", type=float, default=0.0, help="Source start seconds")
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Seconds to take from --start (default: --frames / 16)",
    )
    parser.add_argument(
        "--frames",
        type=int,
        default=81,
        help="Used to pick duration at 16 fps when --duration is omitted",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--steps", type=int, default=None, help="Sampler steps; workflow default is 4")
    parser.add_argument("--cfg", type=float, default=None, help="Workflow default is 1")
    parser.add_argument("--shift", type=float, default=None, help="Workflow default is 5")
    parser.add_argument("--negative", default=DEFAULT_NEGATIVE)
    parser.add_argument("--no-audio", action="store_true")
    parser.add_argument("--comfy-root", type=Path, default=DEFAULT_COMFY_ROOT)
    parser.add_argument("--comfy-url", default=DEFAULT_COMFY_URL)
    parser.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Trim/check inputs and print the patched job without calling Comfy",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        output = generate_animate(
            args.prompt,
            args.out,
            args.video,
            image=args.image,
            start=args.start,
            duration=args.duration,
            frames=args.frames,
            seed=args.seed,
            steps=args.steps,
            cfg=args.cfg,
            shift=args.shift,
            negative=args.negative,
            keep_audio=not args.no_audio,
            comfy_root=args.comfy_root,
            comfy_url=args.comfy_url,
            workflow_path=args.workflow,
            dry_run=args.dry_run,
        )
    except subprocess.CalledProcessError as exc:
        err = exc.stderr or exc.stdout or str(exc)
        print(err, file=sys.stderr)
        return 1
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
