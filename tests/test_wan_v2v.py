import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.wan_v2v import (
    ANIMATE_FPS,
    AnimateJob,
    apply_job,
    fit_size,
    missing_checkpoint_files,
    wan_frame_count,
)


def test_wan_frame_count_is_4n_plus_1():
    assert wan_frame_count(1) == 1
    assert wan_frame_count(80) == 77
    assert wan_frame_count(81) == 81
    assert wan_frame_count(82) == 81
    assert wan_frame_count(0) == 1


def test_fit_size_snaps_and_caps_longest_side():
    assert fit_size(1920, 1080) == (1280, 720)
    assert fit_size(1080, 1920) == (720, 1280)
    assert fit_size(100, 100) == (96, 96)


def _stub_workflow() -> dict:
    return {
        "22": {
            "class_type": "WanVideoModelLoader",
            "inputs": {"model": "Wan2_2-Animate-14B_fp8_e4m3fn_scaled_KJ.safetensors"},
        },
        "57": {"class_type": "LoadImage", "inputs": {"image": "old.png"}},
        "63": {"class_type": "VHS_LoadVideo", "inputs": {"video": "old.mp4"}},
        "65": {
            "class_type": "WanVideoTextEncodeCached",
            "inputs": {
                "positive_prompt": "old",
                "negative_prompt": "old-neg",
            },
        },
        "27": {
            "class_type": "WanVideoSampler",
            "inputs": {"steps": 4, "cfg": 1, "shift": 5, "seed": 1},
        },
        "150": {
            "class_type": "INTConstant",
            "inputs": {"value": 1280},
            "_meta": {"title": "Width"},
        },
        "151": {
            "class_type": "INTConstant",
            "inputs": {"value": 720},
            "_meta": {"title": "Height"},
        },
        "75": {
            "class_type": "VHS_VideoCombine",
            "inputs": {
                "filename_prefix": "grid",
                "frame_rate": 1,
                "save_output": True,
                "images": ["28", 0],
            },
        },
        "186": {
            "class_type": "VHS_VideoCombine",
            "inputs": {
                "filename_prefix": "wan_animate/output",
                "frame_rate": 16,
                "save_output": True,
                "images": ["28", 0],
            },
        },
    }


def test_apply_job_patches_animate_graph():
    job = AnimateJob(
        prompt="the same person, fully clothed",
        negative="blurry",
        video_name="clip.mp4",
        image_name="ref.png",
        prefix="wan_v2v/out_1",
        width=720,
        height=1280,
        seed=7,
        steps=8,
        cfg=1.0,
        shift=5.0,
    )
    graph = apply_job(_stub_workflow(), job)
    assert graph["57"]["inputs"]["image"] == "ref.png"
    assert graph["63"]["inputs"]["video"] == "clip.mp4"
    assert graph["63"]["inputs"]["force_rate"] == ANIMATE_FPS
    assert graph["65"]["inputs"]["positive_prompt"] == "the same person, fully clothed"
    assert graph["65"]["inputs"]["negative_prompt"] == "blurry"
    assert graph["27"]["inputs"]["seed"] == 7
    assert graph["27"]["inputs"]["steps"] == 8
    assert graph["150"]["inputs"]["value"] == 720
    assert graph["151"]["inputs"]["value"] == 1280
    assert graph["186"]["inputs"]["filename_prefix"] == "wan_v2v/out_1"
    assert graph["186"]["inputs"]["save_output"] is True
    assert graph["75"]["inputs"]["save_output"] is False


def test_trim_video_resamples_to_animate_fps(monkeypatch, tmp_path: Path):
    from tools import wan_v2v as mod

    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(list(cmd))
        class Result:
            returncode = 0
        return Result()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    dest = tmp_path / "clip.mp4"
    mod.trim_video(Path("src.mp4"), dest, 1.5, 12.0)
    assert captured, "ffmpeg was not invoked"
    cmd = captured[0]
    assert cmd[:3] == ["ffmpeg", "-y", "-ss"]
    assert "-vf" in cmd
    assert cmd[cmd.index("-vf") + 1] == f"fps={ANIMATE_FPS}"


def test_apply_job_on_exported_character_replace_graph():
    exported = Path("/home/william/Documents/codingProj/ComfyUI/character_replace_api.json")
    if not exported.is_file():
        pytest.skip("character_replace_api.json not present")
    job = AnimateJob(
        prompt="clothed",
        negative="nsfw",
        video_name="scene.mp4",
        image_name="ref.png",
        prefix="wan_v2v/scene_9",
        width=1280,
        height=720,
        seed=3,
        steps=None,
        cfg=None,
        shift=None,
    )
    graph = apply_job(json.loads(exported.read_text()), job)
    assert graph["57"]["inputs"]["image"] == "ref.png"
    assert graph["63"]["inputs"]["video"] == "scene.mp4"
    assert graph["63"]["inputs"]["force_rate"] == ANIMATE_FPS
    assert graph["65"]["inputs"]["positive_prompt"] == "clothed"
    assert graph["27"]["inputs"]["seed"] == 3
    assert graph["27"]["inputs"]["steps"] == 4
    assert graph["186"]["inputs"]["filename_prefix"] == "wan_v2v/scene_9"
    assert graph["186"]["inputs"]["save_output"] is True


def test_missing_checkpoint_files_lists_animate_fp8(tmp_path: Path):
    missing = missing_checkpoint_files(tmp_path)
    assert (
        "models/diffusion_models/Wan2_2-Animate-14B_fp8_e4m3fn_scaled_KJ.safetensors"
        in missing
    )
    target = tmp_path / (
        "models/diffusion_models/Wan2_2-Animate-14B_fp8_e4m3fn_scaled_KJ.safetensors"
    )
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x")
    assert (
        "models/diffusion_models/Wan2_2-Animate-14B_fp8_e4m3fn_scaled_KJ.safetensors"
        not in missing_checkpoint_files(tmp_path)
    )
