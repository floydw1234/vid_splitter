import json
from pathlib import Path

import pytest

from editor.project import (
    Project,
    ProjectValidationError,
    ReplacementEntry,
    Segment,
    load_project,
    save_project,
    validate_project,
)


def test_replacement_metadata_round_trip(tmp_path):
    project = Project(
        id="r1",
        name="r",
        source_video="source.mp4",
        duration=5.0,
        fps=24.0,
        width=320,
        height=180,
        has_audio=True,
        replacement_clips=["a.mp4"],
        replacements=[
            ReplacementEntry(
                name="a.mp4",
                duration=1.5,
                has_audio=False,
                width=640,
                height=360,
            )
        ],
    )
    save_project(tmp_path, project)
    loaded = load_project(tmp_path, "r1")
    assert loaded.replacements[0].has_audio is False
    assert loaded.replacements[0].duration == 1.5


def test_json_round_trip(tmp_path):
    data_dir = tmp_path
    project = Project(
        id="abc123",
        name="demo",
        source_video="source.mp4",
        duration=12.5,
        fps=24.0,
        width=320,
        height=180,
        has_audio=True,
        segments=[
            Segment(
                id="s1",
                start=1.0,
                end=2.0,
                topic="lgbtq",
                score=0.8,
                source="suggested",
                status="suggested",
                action="blur",
            )
        ],
        replacement_clips=["clip.mp4"],
    )
    save_project(data_dir, project)
    loaded = load_project(data_dir, "abc123")
    expected = project.to_dict()
    expected["revision"] = loaded.revision
    assert loaded.to_dict() == expected
    assert loaded.revision == 1


def test_validation_start_ge_end(tmp_path):
    project = Project(
        id="x",
        name="x",
        source_video="s.mp4",
        duration=5.0,
        fps=24.0,
        width=100,
        height=100,
        has_audio=False,
        segments=[Segment(id="a", start=3.0, end=3.0)],
    )
    with pytest.raises(ProjectValidationError):
        validate_project(project)


def test_atomic_save_uses_replace(tmp_path, monkeypatch):
    data_dir = tmp_path
    project = Project(
        id="p1",
        name="p",
        source_video="s.mp4",
        duration=1.0,
        fps=24.0,
        width=10,
        height=10,
        has_audio=False,
    )
    save_project(data_dir, project)
    path = data_dir / "projects" / "p1" / "project.json"
    assert path.is_file()
    calls = []
    real_replace = Path.replace if hasattr(Path, "replace") else None

    def track_replace(self, target):
        calls.append(("replace", str(self), str(target)))
        return Path(self).rename(target)

    monkeypatch.setattr("editor.project.os.replace", lambda src, dst: calls.append(("os.replace", src, dst)))
    save_project(data_dir, project)
    assert any(c[0] == "os.replace" for c in calls)
