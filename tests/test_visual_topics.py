from pathlib import Path

from PIL import Image

from analyzer.visual_topics import APPEARANCE_LABELS, attach_visual_topics


class _FakeScanner:
    def __init__(self, mapping: dict[str, list[str]]):
        self.mapping = mapping
        self.seen: list[str] = []

    def topics_for_path(self, path):
        self.seen.append(str(path))
        return list(self.mapping.get(Path(path).name, []))


def _write_jpg(path: Path) -> Path:
    Image.new("RGB", (32, 32), color=(12, 34, 56)).save(path)
    return path


def test_appearance_labels_cover_transcript_skip_set():
    assert APPEARANCE_LABELS.get("black people") == "black"
    assert APPEARANCE_LABELS.get("asian person") == "asian"


def test_attach_visual_topics_unions_frame_labels_into_buckets(tmp_path: Path):
    cross = _write_jpg(tmp_path / "cross.jpg")
    plain = _write_jpg(tmp_path / "plain.jpg")
    scanner = _FakeScanner({
        "cross.jpg": ["religion_christianity"],
        "plain.jpg": [],
    })
    segments = [
        {"id": "seg_001", "start_time": 0.0, "end_time": 5.0, "topics": []},
        {"id": "seg_002", "start_time": 5.0, "end_time": 10.0, "topics": ["nudity"]},
    ]
    frames = [
        {"time": 2.0, "frame_path": str(cross)},
        {"time": 7.0, "frame_path": str(plain)},
    ]
    result = attach_visual_topics(segments, frames, scanner)
    assert result[0]["topics"] == ["religion_christianity"]
    assert result[1]["topics"] == ["nudity"]
    assert len(scanner.seen) == 2


def test_attach_visual_topics_tags_shared_segment_boundary(tmp_path: Path):
    cross = _write_jpg(tmp_path / "cross.jpg")
    scanner = _FakeScanner({"cross.jpg": ["black people"]})
    segments = [
        {"id": "seg_001", "start_time": 30.0, "end_time": 35.0, "topics": []},
        {"id": "seg_002", "start_time": 35.0, "end_time": 40.0, "topics": []},
    ]
    frames = [{"time": 35.0, "frame_path": str(cross)}]
    result = attach_visual_topics(segments, frames, scanner)
    assert result[0]["topics"] == ["black people"]
    assert result[1]["topics"] == ["black people"]
