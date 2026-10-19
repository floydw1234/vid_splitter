import os
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from editor.app import create_app
from editor.ffmpeg_util import ffmpeg_available


@pytest.fixture
def client(tmp_path, monkeypatch):
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


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg required")
def test_create_from_media_and_range(client):
    res = client.post("/api/projects", json={"media": "sample.mp4"})
    assert res.status_code == 200
    pid = res.json()["id"]
    vid = client.get(f"/api/projects/{pid}/video")
    assert vid.status_code == 200
    headers = {"Range": "bytes=0-99"}
    partial = client.get(f"/api/projects/{pid}/video", headers=headers)
    assert partial.status_code == 206
    assert partial.headers.get("accept-ranges") == "bytes"
    assert "content-range" in partial.headers


def test_path_traversal_rejected(client):
    res = client.post("/api/projects", json={"media": "../secret.mp4"})
    assert res.status_code == 400


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg required")
def test_segment_crud(client):
    pid = client.post("/api/projects", json={"media": "sample.mp4"}).json()["id"]
    seg = client.post(
        f"/api/projects/{pid}/segments",
        json={"start": 0.5, "end": 1.5, "source": "manual", "status": "accepted"},
    )
    assert seg.status_code == 200
    sid = seg.json()["id"]
    patched = client.patch(
        f"/api/projects/{pid}/segments/{sid}",
        json={"action": "blur"},
    )
    assert patched.status_code == 200
    assert patched.json()["action"] == "blur"
    deleted = client.delete(f"/api/projects/{pid}/segments/{sid}")
    assert deleted.status_code == 200


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg required")
def test_fake_detection_job(client):
    pid = client.post("/api/projects", json={"media": "sample.mp4"}).json()["id"]
    det = client.post(
        f"/api/projects/{pid}/detect",
        json={"topics": ["asian people"], "threshold": 0.5, "interval": 1.0},
    )
    job_id = det.json()["job_id"]
    for _ in range(60):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            break
        time.sleep(0.2)
    assert job["status"] == "done"
    project = client.get(f"/api/projects/{pid}").json()
    assert isinstance(project["segments"], list)


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg required")
@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg required")
def test_detect_concurrent_returns_409(client, monkeypatch):
    from editor import detect as detect_mod

    real_run = detect_mod.run_detection

    def slow_run(*args, **kwargs):
        time.sleep(2.0)
        return real_run(*args, **kwargs)

    monkeypatch.setattr("editor.app.run_detection", slow_run)
    pid = client.post("/api/projects", json={"media": "sample.mp4"}).json()["id"]
    first = client.post(
        f"/api/projects/{pid}/detect",
        json={"topics": ["asian people"], "threshold": 0.5, "interval": 2.0},
    )
    assert first.status_code == 200
    time.sleep(0.15)
    second = client.post(
        f"/api/projects/{pid}/detect",
        json={"topics": ["lgbtq"], "threshold": 0.5, "interval": 2.0},
    )
    assert second.status_code == 409
    body = second.json()
    assert body["detail"]["job_id"] == first.json()["job_id"]


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg required")
def test_detection_job_reports_error(client, monkeypatch):
    pid = client.post("/api/projects", json={"media": "sample.mp4"}).json()["id"]

    def boom(*_args, **_kwargs):
        raise RuntimeError("clef model load failure")

    monkeypatch.setattr("editor.app.run_detection", boom)
    job_id = client.post(f"/api/projects/{pid}/detect", json={"topics": ["lgbtq"]}).json()[
        "job_id"
    ]
    for _ in range(30):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] == "error":
            break
        time.sleep(0.1)
    assert job["status"] == "error"
    assert "clef model load failure" in (job["error"] or job["message"])


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg required")
def test_generate_replacement_requires_body(client, monkeypatch):
    pid = client.post("/api/projects", json={"media": "sample.mp4"}).json()["id"]
    sid = client.post(
        f"/api/projects/{pid}/segments",
        json={"start": 0.0, "end": 1.0},
    ).json()["id"]
    res = client.post(
        f"/api/projects/{pid}/segments/{sid}/generate-replacement",
        json={"points": []},
    )
    assert res.status_code == 400


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg required")
def test_range_bytes_exact_and_suffix(client):
    pid = client.post("/api/projects", json={"media": "sample.mp4"}).json()["id"]
    full = client.get(f"/api/projects/{pid}/video").content
    partial = client.get(
        f"/api/projects/{pid}/video",
        headers={"Range": "bytes=10-19"},
    )
    assert partial.status_code == 206
    assert partial.content == full[10:20]
    assert partial.headers.get("content-range") == f"bytes 10-19/{len(full)}"
    suffix = client.get(
        f"/api/projects/{pid}/video",
        headers={"Range": "bytes=-5"},
    )
    assert suffix.status_code == 206
    assert suffix.content == full[-5:]
    bad = client.get(
        f"/api/projects/{pid}/video",
        headers={"Range": f"bytes={len(full)}-{len(full) + 99}"},
    )
    assert bad.status_code == 416


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg required")
def test_put_ignores_server_fields(client):
    pid = client.post("/api/projects", json={"media": "sample.mp4"}).json()["id"]
    project = client.get(f"/api/projects/{pid}").json()
    orig_source = project["source_video"]
    project["source_video"] = "../../etc/passwd"
    project["duration"] = 9999.0
    project["name"] = "renamed"
    res = client.put(f"/api/projects/{pid}", json=project)
    assert res.status_code == 200
    again = client.get(f"/api/projects/{pid}").json()
    assert again["source_video"] == orig_source
    assert again["duration"] != 9999.0
    assert again["name"] == "renamed"


def test_invalid_project_id_404(client):
    res = client.get("/api/projects/../../etc/passwd")
    assert res.status_code == 404


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg required")
def test_put_revision_conflict(client):
    pid = client.post("/api/projects", json={"media": "sample.mp4"}).json()["id"]
    project = client.get(f"/api/projects/{pid}").json()
    stale = dict(project)
    stale["revision"] = project["revision"] - 1
    res = client.put(f"/api/projects/{pid}", json=stale)
    assert res.status_code == 409
    project["name"] = "after conflict"
    ok = client.put(f"/api/projects/{pid}", json=project)
    assert ok.status_code == 200
    assert ok.json()["name"] == "after conflict"
