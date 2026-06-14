import struct
from pathlib import Path

import pytest

from vid_splitter.bvf_muxer import (
    ASSET_BLOCK_HEADER_SIZE,
    ASSET_BLOCK_MAGIC,
    BLOCK_HEADER_SIZE,
    BLOCK_MAGIC,
    CONTAINER_FMP4,
    DEFAULT_FLAGS,
    FILE_HEADER_SIZE,
    FILE_MAGIC,
    INDEX_ENTRY_SIZE,
    BvfMuxer,
    _build_block_header,
    _build_media_asset_block,
    _build_packet,
    _container_name,
    _parse_block_header,
)


@pytest.fixture
def profiles():
    return {
        "child": {"name": "Child", "filters": {"gore": "skip", "language": "mute"}},
        "adult": {"name": "Adult", "filters": {}},
    }


@pytest.fixture
def segments():
    return [
        {
            "id": "seg_001",
            "start_time": 0.0,
            "end_time": 2.0,
            "tags": [],
            "risk": "safe",
            "action": "play",
            "media_container": "fmp4",
            "media_payload": b"ftyp....moov....moof....mdat1",
        },
        {
            "id": "seg_002",
            "start_time": 2.0,
            "end_time": 4.0,
            "tags": ["gore"],
            "risk": "mature",
            "action": "skip",
            "media_container": "fmp4",
            "media_payload": b"ftyp....moov....moof....mdat2",
        },
    ]


def test_file_constants_are_production_asset_model():
    assert FILE_MAGIC == b"BVF\x01\x00\x00\x00\x00"
    assert BLOCK_MAGIC == b"BVA\x00"
    assert ASSET_BLOCK_MAGIC == b"BVA\x00"
    assert FILE_HEADER_SIZE == 64
    assert INDEX_ENTRY_SIZE == 40
    assert BLOCK_HEADER_SIZE == 32
    assert ASSET_BLOCK_HEADER_SIZE == 32
    assert CONTAINER_FMP4 == 1
    assert _container_name(CONTAINER_FMP4) == "fmp4"


def test_block_header_records_container():
    header = _build_block_header("seg_001", "fmp4")
    magic, segment_id, container, flags, reserved = struct.unpack("<4s 16s III", header)
    assert magic == ASSET_BLOCK_MAGIC
    assert segment_id.rstrip(b"\x00") == b"seg_001"
    assert container == CONTAINER_FMP4
    assert flags == 0
    assert reserved == 0


def test_media_asset_block_contains_payload_after_header():
    payload = b"ftypmp42moofmdat"
    block = _build_media_asset_block("seg_001", payload, "fmp4")
    assert block[:4] == ASSET_BLOCK_MAGIC
    assert block[ASSET_BLOCK_HEADER_SIZE:] == payload
    parsed = _parse_block_header(block)
    assert parsed["segment_id"] == "seg_001"
    assert parsed["container"] == "fmp4"


def test_raw_packet_builder_is_not_supported():
    with pytest.raises(ValueError, match="fMP4/CMAF"):
        _build_packet(1, b"data", 0)


def test_bvf_muxer_header_contains_semantics_clarifying_comment():
    muxer_path = Path(__file__).resolve().parents[1] / "vid_splitter" / "bvf_muxer.py"
    header_lines = muxer_path.read_text(encoding="utf-8").splitlines()[:40]
    header_text = "\n".join(header_lines).lower()

    assert "container metadata" in header_text
    assert "analysis semantics" in header_text


def test_write_and_read_bvf_asset_blocks(tmp_path: Path, segments, profiles):
    path = BvfMuxer(movie_id="movie", title="Movie").write_bvf(
        tmp_path / "movie.bvf",
        segments=segments,
        duration_seconds=4.0,
        profiles=profiles,
    )

    parsed = BvfMuxer.read_bvf(path)
    assert parsed["header"]["magic"] == FILE_MAGIC.decode("ascii")
    assert parsed["header"]["flags"] == DEFAULT_FLAGS
    assert parsed["header"]["segment_count"] == 2
    assert parsed["manifest"]["media_model"] == "asset-blocks"
    assert parsed["manifest"]["preferred_container"] == "fmp4"
    assert parsed["manifest"]["segments"][0]["media"]["container"] == "fmp4"
    assert parsed["asset_headers"][0]["container"] == "fmp4"

    with path.open("rb") as f:
        first_index = parsed["segments"][0]
        f.seek(first_index["data_offset"] + ASSET_BLOCK_HEADER_SIZE)
        assert f.read(len(segments[0]["media_payload"])) == segments[0]["media_payload"]


def test_profile_resolution_entries_are_embedded(tmp_path: Path, segments, profiles):
    path = BvfMuxer().write_bvf(
        tmp_path / "movie.bvf",
        segments=segments,
        duration_seconds=4.0,
        profiles=profiles,
    )
    manifest = BvfMuxer.read_bvf(path)["manifest"]
    mature = manifest["segments"][1]
    assert mature["profiles"]["child"]["action"] == "skip"
    assert mature["profiles"]["adult"]["action"] == "play"
