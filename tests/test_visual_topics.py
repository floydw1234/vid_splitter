from pathlib import Path

from PIL import Image

from analyzer.visual_topics import (
    PERSON_CROP_STRICT_GROUP_INDEXES,
    VISUAL_PROMPTS,
    ClipTopicScanner,
    appearance_class_for_label,
    attach_visual_topics,
    clip_label_groups,
    crop_windows,
    extra_visual_prompt,
    gated_inset_person_crops,
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


def test_gated_inset_person_crops_selects_between_primary_and_inset_thresholds():
    large = Image.new("RGB", (40, 40), color="white")
    medium = Image.new("RGB", (20, 20), color="gray")
    tiny = Image.new("RGB", (8, 8), color="black")
    crops = [(large, 42.0), (medium, 18.0), (tiny, 4.0)]
    insets = gated_inset_person_crops(
        crops,
        primary_min_ratio=0.50,
        inset_min_ratio=0.10,
    )
    assert [area for _crop, area in insets] == [18.0]


class _AppearanceTestScanner(ClipTopicScanner):
    """Mock scanner: inject crops and CLIP winners without loading models."""

    def __init__(self, *, primary_crops, all_crops, winners_by_area):
        super().__init__(
            taxonomy={**VISUAL_PROMPTS, "black people": "black people", "asian people": "asian people"},
            gated_inset_black=True,
        )
        self._test_primary_crops = primary_crops
        self._test_all_crops = all_crops
        self._test_winners = winners_by_area
        self._appearance_keys = {"black people": "black", "asian people": "asian"}

    def _ensure_model(self) -> None:
        return

    def _person_crops_for_scoring(self, image, *, primary_only=True):
        _ = image
        return list(self._test_primary_crops if primary_only else self._test_all_crops)

    def _argmax_class(self, crop, classes, texts):
        _ = classes, texts
        area = next(
            (area for c, area in self._test_all_crops if c is crop),
            float(crop.size[0] * crop.size[1]),
        )
        return self._test_winners.get(area)


def test_gated_inset_small_crop_adds_black_only():
    large = Image.new("RGB", (40, 40), color="white")
    inset = Image.new("RGB", (10, 10), color="black")
    scanner = _AppearanceTestScanner(
        primary_crops=[(large, 42.0)],
        all_crops=[(large, 42.0), (inset, 18.0)],
        winners_by_area={42.0: "other", 18.0: "black"},
    )
    image = Image.new("RGB", (64, 64), color="white")
    assert scanner._primary_appearance_hits(image) == []
    assert scanner._gated_black_inset_hits(image) == ["black people"]
    assert scanner._appearance_hits(image) == ["black people"]


def test_gated_inset_small_crop_cannot_add_asian():
    large = Image.new("RGB", (40, 40), color="white")
    inset = Image.new("RGB", (10, 10), color="black")
    scanner = _AppearanceTestScanner(
        primary_crops=[(large, 42.0)],
        all_crops=[(large, 42.0), (inset, 18.0)],
        winners_by_area={42.0: "other", 18.0: "asian"},
    )
    image = Image.new("RGB", (64, 64), color="white")
    assert scanner._gated_black_inset_hits(image) == []
    assert scanner._appearance_hits(image) == []


def test_primary_appearance_path_unchanged_for_normal_sized_crops():
    large = Image.new("RGB", (40, 40), color="white")
    co_primary = Image.new("RGB", (30, 30), color="gray")
    scanner = _AppearanceTestScanner(
        primary_crops=[(large, 42.0), (co_primary, 27.0)],
        all_crops=[(large, 42.0), (co_primary, 27.0)],
        winners_by_area={42.0: "other", 27.0: "asian"},
    )
    image = Image.new("RGB", (64, 64), color="white")
    assert scanner._primary_appearance_hits(image) == ["asian people"]
    assert scanner._gated_black_inset_hits(image) == []
    assert scanner._appearance_hits(image) == ["asian people"]


def test_gated_inset_disabled_skips_inset_pass():
    large = Image.new("RGB", (40, 40), color="white")
    inset = Image.new("RGB", (10, 10), color="black")
    scanner = _AppearanceTestScanner(
        primary_crops=[(large, 42.0)],
        all_crops=[(large, 42.0), (inset, 18.0)],
        winners_by_area={42.0: "other", 18.0: "black"},
    )
    scanner.gated_inset_black = False
    image = Image.new("RGB", (64, 64), color="white")
    assert scanner._appearance_hits(image) == []
