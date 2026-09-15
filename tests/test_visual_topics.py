from pathlib import Path

from PIL import Image

from analyzer.visual_topics import (
    PERSON_CROP_STRICT_GROUP_INDEXES,
    VISUAL_PROMPTS,
    appearance_class_for_label,
    attach_visual_topics,
    clip_label_groups,
    crop_windows,
    extra_visual_prompt,
    keep_primary_person_crops,
    strict_visual_groups,
)


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


def test_clip_label_groups_keeps_custom_labels_out_of_builtin_softmax():
    taxonomy = {**VISUAL_PROMPTS, "asian people": "asian people", "lesbians": "lesbians"}
    builtin_keys, builtin_prompts, extra_keys, extra_prompts = clip_label_groups(taxonomy)
    assert "nudity" in builtin_keys
    assert extra_keys == ["asian people", "lesbians"]
    assert extra_prompts == [
        extra_visual_prompt("asian people"),
        extra_visual_prompt("lesbians"),
    ]
    assert extra_visual_prompt("asian people") == "a photo of an East Asian person"
    assert "two women kissing" in extra_visual_prompt("lesbians")
    assert appearance_class_for_label("black people") == "black"
    assert appearance_class_for_label("lesbians") is None
    assert "lgbtq" not in builtin_keys
    assert "violence_domestic" not in builtin_keys
    assert len(builtin_prompts) == len(builtin_keys)
    groups = strict_visual_groups()
    assert "lgbtq" in groups
    assert "violence_domestic" in groups
    assert all(group[0] for group in groups["lgbtq"])
    assert groups["lgbtq"][0][0].startswith("a photograph of a rainbow")
    assert "beanie and lip piercing" in groups["lgbtq"][1][0]
    assert PERSON_CROP_STRICT_GROUP_INDEXES["lgbtq"] == {1}


def test_keep_primary_person_crops_drops_split_screen_insets():
    large = Image.new("RGB", (40, 40), color="white")
    small = Image.new("RGB", (10, 10), color="black")
    kept = keep_primary_person_crops([(small, 18.0), (large, 42.0)])
    assert [area for _crop, area in kept] == [42.0]
    both = keep_primary_person_crops([(small, 27.0), (large, 41.0)])
    assert sorted(area for _crop, area in both) == [27.0, 41.0]


def test_crop_windows_covers_full_frame_and_regions():
    image = Image.new("RGB", (40, 20), color="white")
    crops = crop_windows(image)
    assert len(crops) == 6
    assert crops[0].size == (40, 20)


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
