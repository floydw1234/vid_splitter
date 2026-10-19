"""Tests for AI replacement generation (no ComfyUI / network)."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from editor.app import create_app
from editor.comfy import find_output_media, queue_is_idle
from editor.ffmpeg_util import ffmpeg_available
import editor.generate as generate_mod
from editor.generate import (
    MAX_SEGMENT_SECONDS,
    generate_replacement,
    patch_flux_workflow,
    patch_sam2_node,
    patch_wan_workflow,
    postprocess_to_project_spec,
    scale_sam2_points,
    validate_generate_inputs,
    wait_for_comfy_queue_idle,
)
from editor.project import Project, Segment, load_project, project_dir, save_project

WAN_WORKFLOW = Path(__file__).resolve().parents[1] / "editor/workflows/wan_animate_replace_api.json"
FLUX_WORKFLOW = Path(__file__).resolve().parents[1] / "editor/workflows/flux_klein_ref_api.json"


@pytest.fixture
def client(tmp_path, monkeypatch):
    import subprocess

    monkeypatch.setenv("VID_EDITOR_FAKE_DETECTOR", "1")
    media = tmp_path / "media"
    media.mkdir()
    data = tmp_path / "data"
    if ffmpeg_available():
        clip = media / "sample.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=160x90:rate=24:duration=4",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=220:duration=4",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                str(clip),
            ],
            check=True,
            capture_output=True,
        )
    app = create_app(data, media)
    return TestClient(app)


def _sample_project() -> Project:
    return Project(
        id="p1",
        name="test",
        source_video="source.mp4",
        duration=30.0,
        fps=24.0,
        width=1920,
        height=1080,
        has_audio=True,
    )


def _segment(start: float = 0.0, end: float = 2.0) -> Segment:
    return Segment(id="seg1", start=start, end=end)


def test_scale_sam2_points_and_patch_removes_bboxes():
    pos, neg = scale_sam2_points([[0.5, 0.25, 1], [0.1, 0.9, 0]], 832, 464)
    assert pos == [{"x": 416, "y": 116}]
    assert neg == [{"x": 83, "y": 418}]
    wf = json.loads(WAN_WORKFLOW.read_text())
    patch_sam2_node(wf, [[0.5, 0.5, 1]], 832, 464)
    ins = wf["104"]["inputs"]
    assert "bboxes" not in ins
    assert json.loads(ins["coordinates_positive"]) == [{"x": 416, "y": 232}]


def test_patch_wan_workflow_sets_ids():
    wf = json.loads(WAN_WORKFLOW.read_text())
    patched = patch_wan_workflow(
        wf,
        video_name="clip.mp4",
        image_name="ref.png",
        positive_prompt="a dancer",
        seed=99,
        width=832,
        height=464,
        points=[[0.2, 0.3, 1]],
        filename_prefix="vid_splitter_editor/gen_seg1_99",
    )
    assert patched["63"]["inputs"]["video"] == "clip.mp4"
    assert patched["57"]["inputs"]["image"] == "ref.png"
    assert patched["65"]["inputs"]["positive_prompt"] == "a dancer"
    assert patched["27"]["inputs"]["seed"] == 99
    assert patched["150"]["inputs"]["value"] == 832
    assert patched["151"]["inputs"]["value"] == 464
    assert patched["186"]["inputs"]["filename_prefix"] == "vid_splitter_editor/gen_seg1_99"
    assert "bboxes" not in patched["104"]["inputs"]


def test_patch_flux_prepends_portrait_prompt():
    wf = json.loads(FLUX_WORKFLOW.read_text())
    patched = patch_flux_workflow(
        wf,
        prompt="red hair",
        seed=7,
        width=768,
        height=1024,
        filename_prefix="vid_splitter_editor/flux_x",
    )
    assert "full body portrait" in patched["5"]["inputs"]["value"]
    assert "red hair" in patched["5"]["inputs"]["value"]
    assert patched["10"]["inputs"]["width"] == 768
    assert patched["13"]["inputs"]["seed"] == 7


def test_validation_duration_and_points():
    project = _sample_project()
    seg = _segment(end=MAX_SEGMENT_SECONDS + 0.1)
    with pytest.raises(ValueError, match="10"):
        validate_generate_inputs(project, seg, [[0.5, 0.5, 1]], ref_image_path=None, prompt="x")
    seg_ok = _segment(end=2.0)
    with pytest.raises(ValueError, match="positive"):
        validate_generate_inputs(project, seg_ok, [[0.5, 0.5, 0]], ref_image_path=None, prompt="x")
    with pytest.raises(ValueError, match="reference"):
        validate_generate_inputs(project, seg_ok, [[0.5, 0.5, 1]], ref_image_path=None, prompt="")


def test_wait_for_comfy_queue_idle_polls():
    client = MagicMock()
    client.queue_status.side_effect = [
        {"queue_running": [1], "queue_pending": []},
        {"queue_running": [], "queue_pending": []},
    ]
    messages: list[str] = []

    def cb(_stage: str, msg: str, **_kw):
        messages.append(msg)

    wait_for_comfy_queue_idle(client, cb, poll_interval=0.01)
    assert messages == ["waiting for ComfyUI queue"]
    assert client.queue_status.call_count == 2


def test_queue_is_idle_helper():
    assert queue_is_idle({"queue_running": [], "queue_pending": []})
    assert not queue_is_idle({"queue_running": [1], "queue_pending": []})


def test_find_output_media_ignores_temp_and_prefers_node():
    outputs = {
        "75": {"images": [{"filename": "mask_preview.png", "type": "temp"}]},
        "186": {
            "gifs": [{"filename": "wan_out.mp4", "type": "output", "subfolder": "wan"}],
        },
    }
    assert find_output_media(outputs) == ("wan_out.mp4", "wan", "output")
    assert find_output_media(outputs, prefer_node="186") == ("wan_out.mp4", "wan", "output")

    only_temp = {
        "75": {"images": [{"filename": "x.png", "type": "temp"}]},
        "186": {"gifs": [{"filename": "y.gif", "type": "temp"}]},
    }
    assert find_output_media(only_temp) is None

    fallback = {
        **only_temp,
        "200": {"videos": [{"filename": "saved.mp4", "type": "output"}]},
    }
    assert find_output_media(fallback, prefer_node="186") == ("saved.mp4", "", "output")

    flux_like = {
        "15": {"images": [{"filename": "ref.png", "type": "output"}]},
        "20": {"images": [{"filename": "other.png", "type": "output"}]},
    }
    assert find_output_media(flux_like, prefer_node="15")[0] == "ref.png"


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg required")
def test_generate_replacement_frees_gpu_before_wan_with_ref_image(tmp_path, monkeypatch):
    import subprocess

    data_dir = tmp_path / "data"
    media = tmp_path / "media"
    media.mkdir()
    clip = media / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x90:rate=24:duration=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:duration=4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(clip),
        ],
        check=True,
        capture_output=True,
    )
    project = _sample_project()
    project.id = "p_free"
    pdir = project_dir(data_dir, project.id)
    pdir.mkdir(parents=True)
    shutil.copy(clip, pdir / "source.mp4")
    ref = pdir / "ref.png"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=64x64",
            "-frames:v",
            "1",
            str(ref),
        ],
        check=True,
        capture_output=True,
    )
    save_project(data_dir, project)

    comfy = MagicMock()
    comfy.queue_status.return_value = {"queue_running": [], "queue_pending": []}
    comfy.upload_file.side_effect = lambda p: p.name

    call_order: list[str] = []
    comfy.free.side_effect = lambda: call_order.append("free")

    raw_mp4 = clip.read_bytes()

    def fake_run(_client, _graph, _cb, **kw):
        stage = kw.get("stage", "")
        call_order.append(f"run:{stage}")
        return raw_mp4, "fake.mp4"

    monkeypatch.setattr(generate_mod, "_run_comfy_media_job", fake_run)

    progress: list[tuple[str, str]] = []

    def track_progress(stage: str, msg: str, **_kw):
        progress.append((stage, msg))

    generate_replacement(
        data_dir,
        project,
        _segment(end=1.0),
        points=[[0.5, 0.5, 1]],
        ref_image_path=ref,
        prompt=None,
        seed=11,
        client=comfy,
        progress_cb=track_progress,
    )

    assert "free" in call_order
    wan_runs = [i for i, c in enumerate(call_order) if c == "run:wan"]
    assert wan_runs
    assert call_order.index("free") < wan_runs[0]
    assert ("wan", "freeing GPU memory") in progress
    comfy.free.assert_called_once()


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg required")
def test_generate_replacement_reload_preserves_concurrent_edits(tmp_path, monkeypatch):
    import subprocess

    data_dir = tmp_path / "data"
    media = tmp_path / "media"
    media.mkdir()
    clip = media / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x90:rate=24:duration=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:duration=4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(clip),
        ],
        check=True,
        capture_output=True,
    )
    project = Project(
        id="p_reload",
        name="reload",
        source_video="source.mp4",
        duration=4.0,
        fps=24.0,
        width=160,
        height=90,
        has_audio=True,
        segments=[
            Segment(id="seg1", start=0.0, end=1.0, status="accepted"),
            Segment(id="seg2", start=1.0, end=2.0, status="accepted", action="none"),
        ],
    )
    pdir = project_dir(data_dir, project.id)
    pdir.mkdir(parents=True)
    shutil.copy(clip, pdir / "source.mp4")
    ref = pdir / "ref.png"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=64x64",
            "-frames:v",
            "1",
            str(ref),
        ],
        check=True,
        capture_output=True,
    )
    save_project(data_dir, project)
    stale = load_project(data_dir, project.id)
    seg1 = next(s for s in stale.segments if s.id == "seg1")

    raw_mp4 = clip.read_bytes()

    def fake_comfy_job(_client, _graph, _cb, **_kw):
        return raw_mp4, "fake.mp4"

    monkeypatch.setattr(generate_mod, "_run_comfy_media_job", fake_comfy_job)
    monkeypatch.setattr(generate_mod, "wait_for_comfy_queue_idle", lambda *_a, **_k: None)

    def post_with_disk_edit(video_in, dest, proj, segment, source_video):
        disk = load_project(data_dir, proj.id)
        for s in disk.segments:
            if s.id == "seg2":
                s.action = "blur"
                break
        save_project(data_dir, disk)
        return postprocess_to_project_spec(video_in, dest, proj, segment, source_video)

    monkeypatch.setattr(generate_mod, "postprocess_to_project_spec", post_with_disk_edit)

    generate_replacement(
        data_dir,
        stale,
        seg1,
        points=[[0.5, 0.5, 1]],
        ref_image_path=ref,
        prompt=None,
        seed=42,
        client=MagicMock(),
    )

    saved = load_project(data_dir, project.id)
    seg1_saved = next(s for s in saved.segments if s.id == "seg1")
    seg2_saved = next(s for s in saved.segments if s.id == "seg2")
    assert seg1_saved.action == "replace"
    assert seg1_saved.replacement_clip
    assert seg2_saved.action == "blur"


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg required")
def test_generate_replacement_endpoint_returns_job_id(client, monkeypatch):
    pid = client.post("/api/projects", json={"media": "sample.mp4"}).json()["id"]
    sid = client.post(
        f"/api/projects/{pid}/segments",
        json={"start": 0.0, "end": 1.0},
    ).json()["id"]

    def fake_gen(*_a, **_k):
        return Path("gen_fake.mp4")

    monkeypatch.setattr("editor.app.generate_replacement", fake_gen)
    res = client.post(
        f"/api/projects/{pid}/segments/{sid}/generate-replacement",
        json={"points": [[0.5, 0.5, 1]], "prompt": "person"},
    )
    assert res.status_code == 200
    job_id = res.json()["job_id"]
    for _ in range(40):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert job["status"] == "done"


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg required")
def test_postprocess_to_project_spec_has_no_audio_stream(tmp_path):
    import subprocess

    from editor.ffmpeg_util import probe_video

    source = tmp_path / "source.mp4"
    raw = tmp_path / "raw.mp4"
    dest = tmp_path / "out.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x180:rate=24:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x90:rate=24:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(raw),
        ],
        check=True,
        capture_output=True,
    )
    project = _sample_project()
    segment = Segment(id="s1", start=0.0, end=1.0)
    postprocess_to_project_spec(raw, dest, project, segment, source)
    meta = probe_video(dest)
    assert meta["has_audio"] is False
