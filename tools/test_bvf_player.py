from pathlib import Path

import pytest

from tools.bvf_player import BVFPlayer
from vid_splitter.bvf_muxer import ASSET_BLOCK_HEADER_SIZE, BvfMuxer


def _write_fixture(tmp_path: Path) -> Path:
    profiles = {
        "child": {"name": "Child", "filters": {"gore": "skip"}},
        "adult": {"name": "Adult", "filters": {}},
    }
    segments = [
        {
            "id": "seg_001",
            "start_time": 0.0,
            "end_time": 2.0,
            "tags": [],
            "risk": "safe",
            "action": "play",
            "media_container": "fmp4",
            "media_payload": b"ftyp....moov....moof....mdat-safe",
        },
        {
            "id": "seg_002",
            "start_time": 2.0,
            "end_time": 4.0,
            "tags": ["gore"],
            "risk": "mature",
            "action": "skip",
            "media_container": "fmp4",
            "media_payload": b"ftyp....moov....moof....mdat-mature",
        },
    ]
    return BvfMuxer(movie_id="fixture", title="Fixture").write_bvf(
        tmp_path / "fixture.bvf",
        segments=segments,
        duration_seconds=4.0,
        profiles=profiles,
    )


def test_extract_segment_returns_media_payload(tmp_path: Path):
    bvf = _write_fixture(tmp_path)
    out = tmp_path / "seg.mp4"
    player = BVFPlayer(bvf, profile="adult")

    assert player.extract_segment("seg_001", out)
    assert out.read_bytes() == b"ftyp....moov....moof....mdat-safe"


def test_dry_sequence_skips_child_mature_segment(tmp_path: Path):
    bvf = _write_fixture(tmp_path)
    player = BVFPlayer(bvf, profile="child")

    sequence = player.resolve_playback_sequence()
    assert [entry["target_id"] for entry in sequence] == ["seg_001"]


def test_adult_sequence_keeps_all_segments(tmp_path: Path):
    bvf = _write_fixture(tmp_path)
    player = BVFPlayer(bvf, profile="adult")

    sequence = player.resolve_playback_sequence()
    assert [entry["target_id"] for entry in sequence] == ["seg_001", "seg_002"]


def test_index_lengths_include_asset_header(tmp_path: Path):
    bvf = _write_fixture(tmp_path)
    parsed = BvfMuxer.read_bvf(bvf)

    assert parsed["segments"][0]["data_length"] == (
        ASSET_BLOCK_HEADER_SIZE + len(b"ftyp....moov....moof....mdat-safe")
    )


@pytest.mark.parametrize("unsupported_action", ["mute", "blur"])
def test_resolve_playback_sequence_rejects_unsupported_actions(
    tmp_path: Path,
    unsupported_action: str,
):
    profiles = {
        "child": {"name": "Child", "filters": {"language": unsupported_action}},
        "adult": {"name": "Adult", "filters": {}},
    }
    segments = [
        {
            "id": "seg_001",
            "start_time": 0.0,
            "end_time": 2.0,
            "tags": ["language"],
            "risk": "mature",
            "action": unsupported_action,
            "media_container": "fmp4",
            "media_payload": b"ftyp....moov....moof....mdat-mature",
        },
    ]
    bvf = BvfMuxer(movie_id="fixture", title="Fixture").write_bvf(
        tmp_path / "fixture.bvf",
        segments=segments,
        duration_seconds=2.0,
        profiles=profiles,
    )

    player = BVFPlayer(bvf, profile="child")

    with pytest.raises(
        ValueError,
        match=rf"Unsupported BVF action for runtime playback: '{unsupported_action}'",
    ):
        player.resolve_playback_sequence()
