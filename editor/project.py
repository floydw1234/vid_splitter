"""Project model, validation, and atomic JSON persistence."""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

SCHEMA_VERSION = 1
PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

SegmentSource = Literal["suggested", "manual"]
SegmentStatus = Literal["suggested", "accepted"]
SegmentAction = Literal["none", "cut", "blur", "black", "mute", "replace"]
ReplacementMode = Literal["trim", "loop"]

VALID_ACTIONS = frozenset({"none", "cut", "blur", "black", "mute", "replace"})
VALID_SOURCES = frozenset({"suggested", "manual"})
VALID_STATUSES = frozenset({"suggested", "accepted"})
VALID_REPLACEMENT_MODES = frozenset({"trim", "loop"})


@dataclass
class ReplacementEntry:
    name: str
    duration: float
    has_audio: bool
    width: int
    height: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReplacementEntry:
        return cls(
            name=str(data["name"]),
            duration=float(data.get("duration") or 0.0),
            has_audio=bool(data.get("has_audio", True)),
            width=int(data.get("width") or 0),
            height=int(data.get("height") or 0),
        )


@dataclass
class Segment:
    id: str
    start: float
    end: float
    topic: str = ""
    score: float = 0.0
    source: SegmentSource = "manual"
    status: SegmentStatus = "suggested"
    action: SegmentAction = "none"
    replacement_clip: str | None = None
    replacement_mode: ReplacementMode = "trim"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if d["replacement_clip"] is None:
            d.pop("replacement_clip", None)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Segment:
        return cls(
            id=str(data["id"]),
            start=float(data["start"]),
            end=float(data["end"]),
            topic=str(data.get("topic") or ""),
            score=float(data.get("score") or 0.0),
            source=str(data.get("source") or "manual"),
            status=str(data.get("status") or "suggested"),
            action=str(data.get("action") or "none"),
            replacement_clip=data.get("replacement_clip"),
            replacement_mode=str(data.get("replacement_mode") or "trim"),
        )


@dataclass
class Project:
    id: str
    name: str
    source_video: str
    duration: float
    fps: float
    width: int
    height: int
    has_audio: bool
    segments: list[Segment] = field(default_factory=list)
    replacement_clips: list[str] = field(default_factory=list)
    replacements: list[ReplacementEntry] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION
    revision: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "revision": self.revision,
            "id": self.id,
            "name": self.name,
            "source_video": self.source_video,
            "duration": self.duration,
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "has_audio": self.has_audio,
            "segments": [s.to_dict() for s in self.segments],
            "replacement_clips": list(self.replacement_clips),
            "replacements": [r.to_dict() for r in self.replacements],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Project:
        version = int(data.get("schema_version") or SCHEMA_VERSION)
        if version != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version: {version}")
        segments = [Segment.from_dict(s) for s in data.get("segments") or []]
        replacements = [
            ReplacementEntry.from_dict(r) for r in data.get("replacements") or []
        ]
        replacement_clips = list(data.get("replacement_clips") or [])
        if not replacement_clips and replacements:
            replacement_clips = [r.name for r in replacements]
        return cls(
            id=str(data["id"]),
            name=str(data.get("name") or data["id"]),
            source_video=str(data["source_video"]),
            duration=float(data["duration"]),
            fps=float(data.get("fps") or 24.0),
            width=int(data["width"]),
            height=int(data["height"]),
            has_audio=bool(data.get("has_audio", True)),
            segments=segments,
            replacement_clips=replacement_clips,
            replacements=replacements,
            schema_version=version,
            revision=int(data.get("revision") or 0),
        )


class ProjectValidationError(ValueError):
    pass


def validate_segment(seg: Segment, duration: float) -> None:
    if seg.source not in VALID_SOURCES:
        raise ProjectValidationError(f"invalid segment source: {seg.source}")
    if seg.status not in VALID_STATUSES:
        raise ProjectValidationError(f"invalid segment status: {seg.status}")
    if seg.action not in VALID_ACTIONS:
        raise ProjectValidationError(f"invalid segment action: {seg.action}")
    if seg.replacement_mode not in VALID_REPLACEMENT_MODES:
        raise ProjectValidationError(f"invalid replacement_mode: {seg.replacement_mode}")
    if seg.start >= seg.end:
        raise ProjectValidationError("segment start must be < end")
    if seg.start < 0 or seg.end > duration:
        raise ProjectValidationError("segment outside video duration")
    if seg.action == "replace" and not seg.replacement_clip:
        raise ProjectValidationError("replace action requires replacement_clip")


def known_replacement_names(project: Project) -> frozenset[str]:
    names = set(project.replacement_clips)
    names.update(r.name for r in project.replacements)
    return frozenset(names)


def validate_segment_replacements(seg: Segment, project: Project) -> None:
    if seg.replacement_clip and seg.replacement_clip not in known_replacement_names(project):
        raise ProjectValidationError(f"unknown replacement_clip: {seg.replacement_clip}")


def validate_project(project: Project) -> None:
    if project.schema_version != SCHEMA_VERSION:
        raise ProjectValidationError("invalid schema_version")
    for seg in project.segments:
        validate_segment(seg, project.duration)


def assert_valid_project_id(project_id: str) -> None:
    if not PROJECT_ID_RE.match(project_id):
        raise FileNotFoundError(project_id)


def project_dir(data_dir: Path, project_id: str) -> Path:
    assert_valid_project_id(project_id)
    return data_dir / "projects" / project_id


def project_json_path(data_dir: Path, project_id: str) -> Path:
    return project_dir(data_dir, project_id) / "project.json"


def load_project(data_dir: Path, project_id: str) -> Project:
    assert_valid_project_id(project_id)
    path = project_json_path(data_dir, project_id)
    if not path.is_file():
        raise FileNotFoundError(project_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    project = Project.from_dict(data)
    validate_project(project)
    return project


def save_project(data_dir: Path, project: Project) -> None:
    validate_project(project)
    project.revision += 1
    pdir = project_dir(data_dir, project.id)
    pdir.mkdir(parents=True, exist_ok=True)
    path = pdir / "project.json"
    payload = json.dumps(project.to_dict(), indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(dir=pdir, prefix=".project.", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def new_segment_id() -> str:
    return uuid.uuid4().hex[:12]


def new_project_id() -> str:
    return uuid.uuid4().hex[:12]
