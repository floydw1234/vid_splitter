import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from editor.detect import extract_frames, run_detection


def test_extract_frames_uses_720p_scale(tmp_path, monkeypatch):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    out_dir = tmp_path / "frames"
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "frame_00001.jpg").write_bytes(b"j")

    monkeypatch.setattr(subprocess, "run", fake_run)
    frames = extract_frames(video, out_dir, interval=2.0, duration=10.0)
    assert frames == [(0.0, out_dir / "frame_00001.jpg")]
    vf = calls[0][calls[0].index("-vf") + 1]
    assert "scale=-2:720" in vf


def test_extract_frames_drops_past_duration(tmp_path, monkeypatch):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    out_dir = tmp_path / "frames"

    def fake_run(cmd, **kwargs):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "frame_00001.jpg").write_bytes(b"j")
        (out_dir / "frame_00002.jpg").write_bytes(b"j")

    monkeypatch.setattr(subprocess, "run", fake_run)
    frames = extract_frames(video, out_dir, interval=2.0, duration=2.0)
    assert len(frames) == 1
    assert frames[0][0] == 0.0


def test_scanner_topics_updated_per_job(tmp_path, monkeypatch):
    monkeypatch.delenv("VID_EDITOR_FAKE_DETECTOR", raising=False)
    scanner = MagicMock()
    scanner.active_labels = frozenset()
    scanner.threshold = 0.0
    monkeypatch.setattr("editor.detect._scanner_cache", scanner)

    def fake_extract(*_a, **_k):
        return [(0.0, Path("/tmp/f.jpg"))]

    monkeypatch.setattr("editor.detect.extract_frames", fake_extract)
    monkeypatch.setattr(
        "editor.detect.score_frame_image",
        lambda *_a, **_k: {"lgbtq": 0.9},
    )
    monkeypatch.setattr("editor.detect.load_project", lambda *_a, **_k: MagicMock(
        duration=5.0, segments=[], source_video="s.mp4"
    ))
    monkeypatch.setattr("editor.detect.save_project", lambda *_a, **_k: None)

    data = tmp_path / "data"
    (data / "projects" / "p1").mkdir(parents=True)
    run_detection(data, "p1", topics=["asian people"], threshold=0.7, interval=2.0)
    assert scanner.active_labels == frozenset(["asian people"])
    assert scanner.threshold == 0.7
