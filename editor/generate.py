"""AI character replacement via ComfyUI (Wan Animate + optional Flux ref)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from editor.comfy import (
    ComfyClient,
    find_output_media,
    prompt_queue_position,
    queue_is_idle,
)
from editor.ffmpeg_util import probe_video
from editor.project import Project, ReplacementEntry, Segment, load_project, project_dir, save_project
from tools.wan_v2v import ANIMATE_FPS, DEFAULT_NEGATIVE, fit_size, mux_audio, trim_video

MAX_SEGMENT_SECONDS = 10.0
WAN_MAX_SIDE = 832
FLUX_PORTRAIT = (768, 1024)
FLUX_SQUARE = (1024, 1024)

WORKFLOW_DIR = Path(__file__).resolve().parent / "workflows"
WAN_WORKFLOW_PATH = WORKFLOW_DIR / "wan_animate_replace_api.json"
FLUX_WORKFLOW_PATH = WORKFLOW_DIR / "flux_klein_ref_api.json"

FLUX_PROMPT_PREFIX = "full body portrait of a single person, plain background, "

_gpu_lock = threading.Lock()


def gpu_lock() -> threading.Lock:
    return _gpu_lock


ProgressCallback = Callable[..., None]


def _default_progress(_stage: str, _message: str, **kwargs: Any) -> None:
    pass


def validate_generate_inputs(
    project: Project,
    segment: Segment,
    points: list[list[float]],
    *,
    ref_image_path: Path | None,
    prompt: str | None,
) -> tuple[int, int]:
    duration = segment.end - segment.start
    if duration > MAX_SEGMENT_SECONDS:
        raise ValueError(
            f"Segment length {duration:.2f}s exceeds the {MAX_SEGMENT_SECONDS:.0f}s limit for AI replacement"
        )
    has_positive = any(len(p) >= 3 and int(p[2]) == 1 for p in points)
    if not has_positive:
        raise ValueError("At least one positive point (label 1) is required")
    if ref_image_path is None and not (prompt and prompt.strip()):
        raise ValueError("Provide a reference image or a text prompt")
    clip_w, clip_h = fit_size(project.width, project.height, max_side=WAN_MAX_SIDE)
    return clip_w, clip_h


def scale_sam2_points(
    points: list[list[float]], width: int, height: int
) -> tuple[list[dict[str, int]], list[dict[str, int]]]:
    positive: list[dict[str, int]] = []
    negative: list[dict[str, int]] = []
    for pt in points:
        if len(pt) < 3:
            continue
        x01, y01, label = float(pt[0]), float(pt[1]), int(pt[2])
        coord = {"x": int(round(x01 * width)), "y": int(round(y01 * height))}
        if label == 1:
            positive.append(coord)
        else:
            negative.append(coord)
    return positive, negative


def patch_sam2_node(workflow: dict[str, Any], points: list[list[float]], width: int, height: int) -> None:
    node = workflow.get("104")
    if not node or not isinstance(node, dict):
        raise KeyError("Sam2Segmentation node 104 not found in workflow")
    inputs = node.setdefault("inputs", {})
    inputs.pop("bboxes", None)
    pos, neg = scale_sam2_points(points, width, height)
    if pos:
        inputs["coordinates_positive"] = json.dumps(pos)
    else:
        inputs.pop("coordinates_positive", None)
    if neg:
        inputs["coordinates_negative"] = json.dumps(neg)
    else:
        inputs.pop("coordinates_negative", None)


def patch_wan_workflow(
    workflow: dict[str, Any],
    *,
    video_name: str,
    image_name: str,
    positive_prompt: str,
    seed: int,
    width: int,
    height: int,
    points: list[list[float]],
    filename_prefix: str,
) -> dict[str, Any]:
    graph = deepcopy(workflow)
    if "57" in graph:
        graph["57"].setdefault("inputs", {})["image"] = image_name
    if "63" in graph:
        ins = graph["63"].setdefault("inputs", {})
        ins["video"] = video_name
        ins["force_rate"] = int(ANIMATE_FPS)
    if "65" in graph:
        ins = graph["65"].setdefault("inputs", {})
        ins["positive_prompt"] = positive_prompt
        ins["negative_prompt"] = DEFAULT_NEGATIVE
    if "27" in graph:
        graph["27"].setdefault("inputs", {})["seed"] = seed
    if "150" in graph:
        graph["150"].setdefault("inputs", {})["value"] = width
    if "151" in graph:
        graph["151"].setdefault("inputs", {})["value"] = height
    patch_sam2_node(graph, points, width, height)
    if "186" in graph:
        ins = graph["186"].setdefault("inputs", {})
        ins["filename_prefix"] = filename_prefix
        ins["save_output"] = True
        ins["trim_to_audio"] = False
        ins.pop("audio", None)
    return graph


def patch_flux_workflow(
    workflow: dict[str, Any],
    *,
    prompt: str,
    seed: int,
    width: int,
    height: int,
    filename_prefix: str,
) -> dict[str, Any]:
    graph = deepcopy(workflow)
    full_prompt = FLUX_PROMPT_PREFIX + prompt.strip()
    if "5" in graph:
        graph["5"].setdefault("inputs", {})["value"] = full_prompt
    if "10" in graph:
        ins = graph["10"].setdefault("inputs", {})
        ins["width"] = width
        ins["height"] = height
    if "11" in graph:
        ins = graph["11"].setdefault("inputs", {})
        ins["width"] = width
        ins["height"] = height
    if "13" in graph:
        graph["13"].setdefault("inputs", {})["seed"] = seed
    if "15" in graph:
        graph["15"].setdefault("inputs", {})["filename_prefix"] = filename_prefix
    return graph


def flux_canvas_size(project: Project) -> tuple[int, int]:
    if project.height > project.width:
        return FLUX_PORTRAIT
    return FLUX_SQUARE


def wait_for_comfy_queue_idle(
    client: ComfyClient,
    progress_cb: ProgressCallback,
    *,
    poll_interval: float = 5.0,
) -> None:
    while True:
        status = client.queue_status()
        if queue_is_idle(status):
            return
        progress_cb("queue", "waiting for ComfyUI queue")
        time.sleep(poll_interval)


def wait_for_prompt(
    client: ComfyClient,
    prompt_id: str,
    progress_cb: ProgressCallback,
    *,
    poll_interval: float = 2.0,
    stage: str = "comfy",
) -> dict[str, Any]:
    started = time.monotonic()
    while True:
        item = client.history(prompt_id)
        status = item.get("status") or {}
        if status.get("status_str") == "error" or status.get("error"):
            raise RuntimeError(f"ComfyUI job failed: {status}")
        if status.get("completed"):
            return item
        elapsed = time.monotonic() - started
        qpos = prompt_queue_position(client.queue_status(), prompt_id)
        if qpos == 0:
            msg = f"ComfyUI running ({elapsed:.0f}s)"
        elif qpos is not None:
            msg = f"ComfyUI queue position {qpos} ({elapsed:.0f}s)"
        else:
            msg = f"ComfyUI processing ({elapsed:.0f}s)"
        progress_cb(stage, msg, elapsed=elapsed)
        time.sleep(poll_interval)


def _extract_segment_audio(source: Path, dest: Path, start: float, duration: float) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{max(0.0, start):.3f}",
            "-i",
            str(source),
            "-t",
            f"{max(0.01, duration):.3f}",
            "-vn",
            "-c:a",
            "aac",
            str(dest),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def postprocess_to_project_spec(
    video_in: Path,
    dest: Path,
    project: Project,
    segment: Segment,
    source_video: Path,
) -> None:
    duration = segment.end - segment.start
    w, h = project.width, project.height
    fps = project.fps
    dest.parent.mkdir(parents=True, exist_ok=True)
    probed = probe_video(video_in)
    in_dur = float(probed["duration"])
    vf_parts = [
        f"scale={w}:{h}:force_original_aspect_ratio=decrease",
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
        f"fps={fps}",
    ]
    if in_dur < duration - 0.02:
        pad_extra = duration - in_dur
        vf_parts.append(f"tpad=stop_mode=clone:stop_duration={pad_extra:.3f}")
    vf = ",".join(vf_parts)
    scaled = dest.with_suffix(".scaled.mp4")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_in),
            "-vf",
            vf,
            "-t",
            f"{duration:.3f}",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(scaled),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    if project.has_audio:
        work = dest.parent
        audio_seg = work / f"_aud_{uuid.uuid4().hex[:8]}.m4a"
        try:
            _extract_segment_audio(source_video, audio_seg, segment.start, duration)
            mux_audio(scaled, audio_seg, dest)
        finally:
            scaled.unlink(missing_ok=True)
            audio_seg.unlink(missing_ok=True)
    else:
        scaled.replace(dest)


def _run_comfy_media_job(
    client: ComfyClient,
    graph: dict[str, Any],
    progress_cb: ProgressCallback,
    *,
    stage: str,
    prefer_node: str | None = None,
) -> tuple[bytes, str]:
    wait_for_comfy_queue_idle(client, progress_cb)
    prompt_id = client.submit(graph)
    item = wait_for_prompt(client, prompt_id, progress_cb, stage=stage)
    outputs = item.get("outputs") or {}
    found = find_output_media(outputs, prefer_node=prefer_node)
    if not found:
        raise RuntimeError("ComfyUI finished but produced no downloadable output")
    filename, subfolder, folder_type = found
    data = client.download_view(filename, subfolder=subfolder, folder_type=folder_type)
    return data, filename


def generate_replacement(
    data_dir: Path,
    project: Project,
    segment: Segment,
    *,
    points: list[list[float]],
    ref_image_path: Path | None,
    prompt: str | None,
    seed: int,
    client: ComfyClient | None = None,
    progress_cb: ProgressCallback | None = None,
) -> Path:
    """
    Generate a replacement clip, register it on the project, and return the clip filename.
    """
    cb = progress_cb or _default_progress
    comfy = client or ComfyClient()
    clip_w, clip_h = validate_generate_inputs(
        project, segment, points, ref_image_path=ref_image_path, prompt=prompt
    )
    if not WAN_WORKFLOW_PATH.is_file():
        raise FileNotFoundError(f"Wan workflow missing: {WAN_WORKFLOW_PATH}")

    pdir = project_dir(data_dir, project.id)
    source = pdir / project.source_video
    if not source.is_file():
        raise FileNotFoundError(f"Project source video missing: {source}")

    seg_duration = segment.end - segment.start
    wan_description = (prompt or "").strip() or "a person"
    out_name = f"gen_{segment.id}_{seed}.mp4"
    repl_dir = pdir / "replacements"
    repl_dir.mkdir(parents=True, exist_ok=True)
    final_path = repl_dir / out_name

    work = Path(tempfile.mkdtemp(prefix="vid_editor_gen_"))
    ref_path = ref_image_path
    try:
        if ref_path is None:
            if not FLUX_WORKFLOW_PATH.is_file():
                raise FileNotFoundError(f"Flux workflow missing: {FLUX_WORKFLOW_PATH}")
            cb("flux", "generating reference image")
            fw, fh = flux_canvas_size(project)
            flux_prefix = f"vid_splitter_editor/flux_{segment.id}_{seed}"
            flux_graph = patch_flux_workflow(
                json.loads(FLUX_WORKFLOW_PATH.read_text()),
                prompt=wan_description,
                seed=seed,
                width=fw,
                height=fh,
                filename_prefix=flux_prefix,
            )
            img_bytes, _ = _run_comfy_media_job(
                comfy, flux_graph, cb, stage="flux", prefer_node="15"
            )
            ref_path = repl_dir / f"ref_{segment.id}_{seed}.png"
            ref_path.write_bytes(img_bytes)
            cb("flux", "reference image ready")
        else:
            if not ref_path.is_file():
                raise FileNotFoundError(f"Reference image not found: {ref_path}")

        cb("prepare", "trimming clip for Wan")
        clip_path = work / "wan_input.mp4"
        trim_video(source, clip_path, segment.start, seg_duration)

        cb("upload", "uploading to ComfyUI")
        video_name = comfy.upload_file(clip_path)
        image_name = comfy.upload_file(ref_path)

        wait_for_comfy_queue_idle(comfy, cb)
        cb("wan", "freeing GPU memory")
        comfy.free()

        wan_prefix = f"vid_splitter_editor/gen_{segment.id}_{seed}"
        wan_graph = patch_wan_workflow(
            json.loads(WAN_WORKFLOW_PATH.read_text()),
            video_name=video_name,
            image_name=image_name,
            positive_prompt=wan_description,
            seed=seed,
            width=clip_w,
            height=clip_h,
            points=points,
            filename_prefix=wan_prefix,
        )
        cb("wan", "queueing Wan Animate")
        raw_bytes, _ = _run_comfy_media_job(
            comfy, wan_graph, cb, stage="wan", prefer_node="186"
        )
        raw_out = work / "wan_raw.mp4"
        raw_out.write_bytes(raw_bytes)

        cb("post", "post-processing video")
        postprocess_to_project_spec(raw_out, final_path, project, segment, source)

        meta = probe_video(final_path)
        entry = ReplacementEntry(
            name=out_name,
            duration=float(meta["duration"]),
            has_audio=bool(meta["has_audio"]),
            width=int(meta["width"]),
            height=int(meta["height"]),
        )
        fresh = load_project(data_dir, project.id)
        fresh.replacements = [r for r in fresh.replacements if r.name != out_name]
        fresh.replacements.append(entry)
        if out_name not in fresh.replacement_clips:
            fresh.replacement_clips.append(out_name)
        for seg in fresh.segments:
            if seg.id == segment.id:
                seg.action = "replace"
                seg.replacement_clip = out_name
                seg.replacement_mode = "trim"
                break
        save_project(data_dir, fresh)
        cb("done", "complete")
        return final_path
    finally:
        shutil.rmtree(work, ignore_errors=True)
