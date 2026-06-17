import struct
from pathlib import Path

import pytest
import zstandard

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


def test_profile_segment_id_routes_nudity_swaps_to_filler_assets(tmp_path: Path):
    profiles = {
        "child": {"name": "Child", "filters": {"nudity": "swap"}},
        "teen_m": {"name": "Teen Male", "filters": {"nudity": "swap"}},
        "teen_f": {"name": "Teen Female", "filters": {"nudity": "swap"}},
        "adult": {"name": "Adult", "filters": {}},
    }
    segments = [
        {
            "id": "seg_001",
            "start_time": 0.0,
            "end_time": 5.0,
            "tags": ["nudity"],
            "risk": "mature",
            "action": "swap",
            "profile_segment_id": "filler_001",
            "media_container": "fmp4",
            "media_payload": b"ftyp....moov....moof....mdat-main",
        },
        {
            "id": "filler_001",
            "start_time": 0.0,
            "end_time": 5.0,
            "tags": [],
            "risk": "safe",
            "action": "play",
            "is_filler": True,
            "media_container": "fmp4",
            "media_payload": b"ftyp....moov....moof....mdat-filler",
        },
    ]

    manifest = BvfMuxer.read_bvf(
        BvfMuxer(movie_id="movie", title="Movie").write_bvf(
            tmp_path / "movie.bvf",
            segments=segments,
            duration_seconds=5.0,
            profiles=profiles,
        )
    )["manifest"]

    mature = next(seg for seg in manifest["segments"] if seg["id"] == "seg_001")
    assert mature["profiles"]["child"] == {"action": "swap", "segment_id": "filler_001"}
    assert mature["profiles"]["teen_m"] == {"action": "swap", "segment_id": "filler_001"}
    assert mature["profiles"]["teen_f"] == {"action": "swap", "segment_id": "filler_001"}
    assert mature["profiles"]["adult"] == {"action": "play", "segment_id": "seg_001"}


def _write_fixture(tmp_path: Path) -> Path:
    profiles = {
        "child": {"name": "Child", "filters": {"nudity": "swap"}},
        "adult": {"name": "Adult", "filters": {}},
    }
    segments = [
        {
            "id": "seg_001",
            "start_time": 0.0,
            "end_time": 2.0,
            "tags": ["nudity"],
            "risk": "mature",
            "action": "swap",
            "profile_segment_id": "filler_001",
            "media_container": "fmp4",
            "media_payload": b"ftyp....moov....moof....mdat-main",
        },
        {
            "id": "filler_001",
            "start_time": 2.0,
            "end_time": 4.0,
            "tags": [],
            "risk": "safe",
            "action": "play",
            "is_filler": True,
            "media_container": "fmp4",
            "media_payload": b"ftyp....moov....moof....mdat-filler",
        },
    ]
    return BvfMuxer(movie_id="fixture", title="Fixture").write_bvf(
        tmp_path / "fixture.bvf",
        segments=segments,
        duration_seconds=4.0,
        profiles=profiles,
    )


def _rewrite_header_field(path: Path, offset: int, fmt: str, value: int) -> None:
    updated = bytearray(path.read_bytes())
    struct.pack_into(fmt, updated, offset, value)
    path.write_bytes(bytes(updated))


def _rewrite_manifest_bytes(path: Path, manifest_bytes: bytes) -> None:
    parsed = BvfMuxer.read_bvf(path)
    raw = path.read_bytes()
    header = parsed["header"]
    manifest_offset = header["manifest_offset"]
    old_manifest_length = header["manifest_length"]
    new_manifest_length = len(manifest_bytes)
    delta = new_manifest_length - old_manifest_length

    updated = bytearray()
    updated.extend(raw[:manifest_offset])
    updated.extend(manifest_bytes)
    updated.extend(raw[manifest_offset + old_manifest_length :])

    struct.pack_into("<Q", updated, 40, new_manifest_length)

    if delta:
        segment_count = header["segment_count"]
        index_offset = header["index_offset"]
        entry_size = INDEX_ENTRY_SIZE
        data_offset_pos = 16
        for idx in range(segment_count):
            entry_start = index_offset + idx * entry_size
            current_offset = struct.unpack_from(
                "<Q", updated, entry_start + data_offset_pos
            )[0]
            struct.pack_into(
                "<Q", updated, entry_start + data_offset_pos, current_offset + delta
            )

    path.write_bytes(bytes(updated))


def test_read_bvf_rejects_truncated_file_shorter_than_header(tmp_path: Path):
    path = tmp_path / "truncated.bvf"
    path.write_bytes(FILE_MAGIC[:8])

    with pytest.raises(Exception, match="header|unpack|buffer|bytes"):
        BvfMuxer.read_bvf(path)


def test_read_bvf_rejects_index_range_beyond_file_size(tmp_path: Path):
    path = _write_fixture(tmp_path)
    _rewrite_header_field(path, 16, "<Q", 10_000)
    _rewrite_header_field(path, 24, "<Q", 10_000)

    with pytest.raises(Exception, match="unpack|buffer|bytes|header"):
        BvfMuxer.read_bvf(path)


def test_read_bvf_rejects_manifest_range_beyond_file_size(tmp_path: Path):
    path = _write_fixture(tmp_path)
    _rewrite_header_field(path, 32, "<Q", 10_000)
    _rewrite_header_field(path, 40, "<Q", 10_000)

    with pytest.raises(Exception, match="decompress|Zstd|json|UTF-8|bytes"):
        BvfMuxer.read_bvf(path)


def test_read_bvf_rejects_segment_data_offset_beyond_eof(tmp_path: Path):
    path = _write_fixture(tmp_path)
    parsed = BvfMuxer.read_bvf(path)
    index_offset = parsed["header"]["index_offset"]
    first_entry_data_offset = index_offset + 16
    _rewrite_header_field(path, first_entry_data_offset, "<Q", 10_000)

    with pytest.raises(Exception, match="header|magic|buffer|bytes"):
        BvfMuxer.read_bvf(path)


def test_read_bvf_rejects_truncated_asset_block_header(tmp_path: Path):
    path = _write_fixture(tmp_path)
    parsed = BvfMuxer.read_bvf(path)
    first_segment = parsed["segments"][0]
    path.write_bytes(path.read_bytes()[: first_segment["data_offset"] + 8])

    with pytest.raises(Exception, match="header|magic|buffer|bytes"):
        BvfMuxer.read_bvf(path)


def test_read_bvf_rejects_corrupt_compressed_manifest_bytes(tmp_path: Path):
    path = _write_fixture(tmp_path)
    corrupt_manifest = zstandard.ZstdCompressor(level=3).compress(b"{not-json")
    _rewrite_manifest_bytes(path, corrupt_manifest)

    with pytest.raises(Exception, match="json|Expecting|decode"):
        BvfMuxer.read_bvf(path)
