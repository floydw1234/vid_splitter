import json
import struct
import zlib
from pathlib import Path
from unittest.mock import patch

import pytest
import zstandard

from vid_splitter.bvf_muxer import (
    BLOCK_HEADER_SIZE,
    BLOCK_MAGIC,
    CODEC_AAC_LC,
    CODEC_AC3,
    CODEC_AV1,
    CODEC_EAC3,
    CODEC_H264,
    CODEC_H265,
    CODEC_OPUS,
    CODEC_VP9,
    CODEC_EAC3,
    DEFAULT_FLAGS,
    FILE_HEADER_SIZE,
    FILE_MAGIC,
    INDEX_ENTRY_SIZE,
    PACKET_AUDIO,
    PACKET_HEADER_SIZE,
    PACKET_SUBTITLE,
    PACKET_VIDEO,
    FLAG_MANIFEST_COMPRESSED,
    FLAG_HAS_CHAPTERS,
    FLAG_HAS_SUBTITLES,
    FLAG_SEEKABLE,
    BvfMuxer,
    _action_to_int,
    _build_block_header,
    _build_file_header,
    _build_index_entry,
    _build_manifest_json,
    _build_packet,
    _build_segment_block,
    _build_stub_segment_block,
    _compress_manifest,
    _pad_segment_id,
    _parse_file_header,
    _parse_index_entry,
    _risk_to_int,
)


# --- Fixtures ---

@pytest.fixture
def muxer(tmp_path):
    return BvfMuxer(movie_id="tt1234567", title="Test Movie")


@pytest.fixture
def sample_segments():
    return [
        {
            "id": "seg_001",
            "start_time": 0.0,
            "end_time": 120.0,
            "tags": [],
            "risk": "safe",
            "action": "play",
        },
        {
            "id": "seg_002",
            "start_time": 120.0,
            "end_time": 180.0,
            "tags": ["violence", "language"],
            "risk": "mature",
            "action": "swap",
            "profile_segment_id": "filler_001",
        },
        {
            "id": "seg_003",
            "start_time": 180.0,
            "end_time": 360.0,
            "tags": [],
            "risk": "safe",
            "action": "play",
        },
    ]


@pytest.fixture
def sample_profiles():
    return {
        "child": {
            "label": "Child (under 13)",
            "filters": ["nudity", "violence", "language", "fear", "gore"],
        },
        "teen": {
            "label": "Teen (13-17)",
            "filters": ["nudity", "gore"],
        },
        "adult": {
            "label": "Adult (18+)",
            "filters": [],
        },
    }


@pytest.fixture
def tmp_bvf(tmp_path):
    """Write a BVF file and return the path."""
    muxer = BvfMuxer(movie_id="tt1234567", title="Test Movie")
    segments = [
        {"id": "seg_001", "start_time": 0.0, "end_time": 120.0, "tags": [], "risk": "safe", "action": "play"},
        {"id": "seg_002", "start_time": 120.0, "end_time": 180.0, "tags": ["violence"], "risk": "mature", "action": "swap", "profile_segment_id": "filler_001"},
        {"id": "seg_003", "start_time": 180.0, "end_time": 360.0, "tags": [], "risk": "safe", "action": "play"},
    ]
    profiles = {
        "child": {"label": "Child (under 13)", "filters": ["nudity", "violence", "language", "fear", "gore"]},
        "teen": {"label": "Teen (13-17)", "filters": ["nudity", "gore"]},
        "adult": {"label": "Adult (18+)", "filters": []},
    }
    return muxer.write_bvf(
        output_path=tmp_path / "test.bvf",
        segments=segments,
        duration_seconds=360.0,
        profiles=profiles,
    )


# --- Codec constants ---

class TestCodecConstants:
    def test_video_codecs(self):
        assert CODEC_H264 == 0x00000001
        assert CODEC_H265 == 0x00000002
        assert CODEC_AV1 == 0x00000003
        assert CODEC_VP9 == 0x00000004

    def test_audio_codecs(self):
        assert CODEC_AAC_LC == 0x00000100
        assert CODEC_OPUS == 0x00000101
        assert CODEC_AC3 == 0x00000102
        assert CODEC_EAC3 == 0x00000103

    def test_packet_types(self):
        assert PACKET_VIDEO == 0x01
        assert PACKET_AUDIO == 0x02
        assert PACKET_SUBTITLE == 0x03

    def test_block_magic(self):
        assert BLOCK_MAGIC == b"SEG\x00"

    def test_file_magic(self):
        assert FILE_MAGIC == b"BVF\x01\x00\x00\x00\x00"

    def test_size_constants(self):
        assert FILE_HEADER_SIZE == 64
        assert INDEX_ENTRY_SIZE == 40
        assert BLOCK_HEADER_SIZE == 32
        assert PACKET_HEADER_SIZE == 16

    def test_manifest_flags(self):
        assert FLAG_MANIFEST_COMPRESSED == 0x00000001
        assert FLAG_HAS_CHAPTERS == 0x00000002
        assert FLAG_HAS_SUBTITLES == 0x00000004
        assert FLAG_SEEKABLE == 0x00000008


# --- _risk_to_int ---

class TestRiskToInt:
    def test_safe(self):
        assert _risk_to_int("safe") == 0

    def test_mature(self):
        assert _risk_to_int("mature") == 1

    def test_restricted(self):
        assert _risk_to_int("restricted") == 2

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown risk level"):
            _risk_to_int("unknown")

    def test_none_raises(self):
        with pytest.raises(ValueError, match="Unknown risk level"):
            _risk_to_int(None)  # type: ignore


# --- _action_to_int ---

class TestActionToInt:
    def test_play(self):
        assert _action_to_int("play") == 0

    def test_swap(self):
        assert _action_to_int("swap") == 1

    def test_skip(self):
        assert _action_to_int("skip") == 2

    def test_mute(self):
        assert _action_to_int("mute") == 3

    def test_blur(self):
        assert _action_to_int("blur") == 4

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown action"):
            _action_to_int("delete")


# --- _pad_segment_id ---

class TestPadSegmentId:
    def test_short_id(self):
        result = _pad_segment_id("seg_001")
        assert result == b"seg_001\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        assert len(result) == 16

    def test_exact_16_char(self):
        result = _pad_segment_id("0123456789abcdef")
        assert len(result) == 16
        assert result == b"0123456789abcdef"

    def test_custom_length(self):
        result = _pad_segment_id("abc", length=8)
        assert result == b"abc\x00\x00\x00\x00\x00"

    def test_too_long_raises(self):
        with pytest.raises(ValueError, match="exceeds max length"):
            _pad_segment_id("a" * 17)


# --- _build_file_header ---

class TestBuildFileHeader:
    def test_size(self):
        header = _build_file_header(
            segment_count=3,
            total_duration_ms=360000,
            index_offset=64,
            index_length=120,
            manifest_offset=184,
            manifest_length=100,
        )
        assert len(header) == FILE_HEADER_SIZE

    def test_magic(self):
        header = _build_file_header(0, 0, 64, 0, 64, 0)
        magic = struct.unpack("<8s", header[:8])[0]
        assert magic == FILE_MAGIC

    def test_version(self):
        header = _build_file_header(0, 0, 64, 0, 64, 0)
        major, minor = struct.unpack("<HH", header[8:12])
        assert major == 1
        assert minor == 0

    def test_segment_count(self):
        header = _build_file_header(
            segment_count=5, total_duration_ms=0,
            index_offset=64, index_length=200,
            manifest_offset=264, manifest_length=100,
        )
        count = struct.unpack("<I", header[48:52])[0]
        assert count == 5

    def test_total_duration_ms(self):
        header = _build_file_header(
            segment_count=0, total_duration_ms=7200000,
            index_offset=64, index_length=0,
            manifest_offset=64, manifest_length=100,
        )
        duration = struct.unpack("<Q", header[52:60])[0]
        assert duration == 7200000

    def test_flags(self):
        header = _build_file_header(
            segment_count=0, total_duration_ms=0,
            index_offset=64, index_length=0,
            manifest_offset=64, manifest_length=0,
            flags=FLAG_HAS_CHAPTERS | FLAG_HAS_SUBTITLES,
        )
        flags = struct.unpack("<I", header[12:16])[0]
        assert flags == (FLAG_HAS_CHAPTERS | FLAG_HAS_SUBTITLES)


# --- _parse_file_header ---

class TestParseFileHeader:
    def test_roundtrip(self):
        header_bytes = _build_file_header(
            segment_count=3, total_duration_ms=360000,
            index_offset=64, index_length=120,
            manifest_offset=184, manifest_length=100,
        )
        parsed = _parse_file_header(header_bytes)
        assert parsed["magic"] == "BVF\x01\x00\x00\x00\x00"
        assert parsed["version_major"] == 1
        assert parsed["version_minor"] == 0
        assert parsed["segment_count"] == 3
        assert parsed["total_duration_ms"] == 360000
        assert parsed["index_offset"] == 64
        assert parsed["index_length"] == 120
        assert parsed["manifest_offset"] == 184
        assert parsed["manifest_length"] == 100

    def test_default_flags(self):
        header_bytes = _build_file_header(
            segment_count=0, total_duration_ms=0,
            index_offset=64, index_length=0,
            manifest_offset=64, manifest_length=0,
        )
        parsed = _parse_file_header(header_bytes)
        assert parsed["flags"] == DEFAULT_FLAGS

    def test_reserved_zero(self):
        header_bytes = _build_file_header(
            segment_count=0, total_duration_ms=0,
            index_offset=64, index_length=0,
            manifest_offset=64, manifest_length=0,
        )
        parsed = _parse_file_header(header_bytes)
        assert parsed["reserved"] == 0


# --- _build_index_entry / _parse_index_entry ---

class TestIndexEntry:
    def test_size(self):
        entry = _build_index_entry("seg_001", data_offset=200, data_length=500, duration_ms=60000)
        assert len(entry) == INDEX_ENTRY_SIZE

    def test_roundtrip(self):
        entry_bytes = _build_index_entry("seg_001", data_offset=200, data_length=500, duration_ms=60000)
        parsed = _parse_index_entry(entry_bytes)
        assert parsed["segment_id"] == "seg_001"
        assert parsed["data_offset"] == 200
        assert parsed["data_length"] == 500
        assert parsed["duration_ms"] == 60000

    def test_long_id_roundtrip(self):
        long_id = "0123456789abcdef"
        entry_bytes = _build_index_entry(long_id, data_offset=0, data_length=100, duration_ms=1000)
        parsed = _parse_index_entry(entry_bytes)
        assert parsed["segment_id"] == long_id

    def test_zero_offset_length(self):
        entry_bytes = _build_index_entry("seg_001", data_offset=0, data_length=0, duration_ms=0)
        parsed = _parse_index_entry(entry_bytes)
        assert parsed["data_offset"] == 0
        assert parsed["data_length"] == 0
        assert parsed["duration_ms"] == 0


# --- _build_packet ---

class TestBuildPacket:
    def test_video_packet(self):
        data = b"\xde\xad\xbe\xef"
        packet = _build_packet(PACKET_VIDEO, data, 1234)
        # packet_type + reserved = 0x00000001
        ptype, psize = struct.unpack("<I I", packet[:8])
        assert ptype == 0x00000001
        assert psize == 4
        pts = struct.unpack("<Q", packet[8:16])[0]
        assert pts == 1234
        assert packet[16:] == data

    def test_audio_packet(self):
        packet = _build_packet(PACKET_AUDIO, b"\x00\x01", 0)
        ptype, psize = struct.unpack("<I I", packet[:8])
        assert ptype == 0x00000002
        assert psize == 2

    def test_empty_data(self):
        packet = _build_packet(PACKET_VIDEO, b"", 999)
        ptype, psize = struct.unpack("<I I", packet[:8])
        assert psize == 0
        pts = struct.unpack("<Q", packet[8:16])[0]
        assert pts == 999

    def test_header_size(self):
        packet = _build_packet(PACKET_VIDEO, b"x", 0)
        assert len(packet) == PACKET_HEADER_SIZE + 1


# --- _build_block_header ---

class TestBuildBlockHeader:
    def test_size(self):
        header = _build_block_header("seg_001")
        assert len(header) == BLOCK_HEADER_SIZE

    def test_magic(self):
        header = _build_block_header("seg_001")
        magic = struct.unpack("<4s", header[:4])[0]
        assert magic == BLOCK_MAGIC

    def test_segment_id(self):
        header = _build_block_header("seg_001")
        sid = struct.unpack("<16s", header[4:20])[0]
        assert sid.rstrip(b"\x00").decode() == "seg_001"

    def test_codecs(self):
        header = _build_block_header("seg_001", codec_video=CODEC_AV1, codec_audio=CODEC_OPUS)
        video_codec, audio_codec, _ = struct.unpack("<III", header[20:32])
        assert video_codec == CODEC_AV1
        assert audio_codec == CODEC_OPUS

    def test_default_codecs(self):
        header = _build_block_header("seg_001")
        video_codec, audio_codec, _ = struct.unpack("<III", header[20:32])
        assert video_codec == CODEC_H264
        assert audio_codec == CODEC_AAC_LC


# --- _build_stub_segment_block ---

class TestBuildStubSegmentBlock:
    def test_contains_block_header(self):
        block = _build_stub_segment_block("seg_001")
        magic = struct.unpack("<4s", block[:4])[0]
        assert magic == BLOCK_MAGIC

    def test_contains_video_packet(self):
        block = _build_stub_segment_block("seg_001")
        # After block header (32 bytes), there should be a video packet
        video_packet = block[32:]
        # Find the audio packet start by looking for 0x00000002
        ptype, _ = struct.unpack("<I I", video_packet[:8])
        assert ptype == 0x00000001  # video marker

    def test_contains_audio_packet(self):
        block = _build_stub_segment_block("seg_001")
        # The block has header + video_packet + audio_packet
        # Find audio packet by scanning for 0x00000002
        assert b"\x02\x00\x00\x00" in block  # PACKET_AUDIO followed by reserved bytes

    def test_custom_codecs(self):
        block = _build_stub_segment_block("seg_001", codec_video=CODEC_AV1, codec_audio=CODEC_OPUS)
        video_codec, audio_codec = struct.unpack("<II", block[20:28])
        assert video_codec == CODEC_AV1
        assert audio_codec == CODEC_OPUS


# --- _build_manifest_json ---

class TestBuildManifestJson:
    def test_required_fields(self):
        data = _build_manifest_json(
            movie_id="tt123", title="T", duration_ms=1000,
            segments=[{"id": "s1"}], profiles={"p": {"label": "P", "filters": []}},
        )
        manifest = json.loads(data.decode("utf-8"))
        assert manifest["bvf_version"] == "1.0"
        assert manifest["movie_id"] == "tt123"
        assert manifest["title"] == "T"
        assert manifest["duration_ms"] == 1000
        assert "analyzed_at" in manifest
        assert "profiles" in manifest
        assert "segments" in manifest

    def test_video_info(self):
        data = _build_manifest_json(
            movie_id="tt123", title="T", duration_ms=1000,
            segments=[], profiles={},
            video_info={"width": 1920, "height": 1080},
        )
        manifest = json.loads(data.decode("utf-8"))
        assert manifest["video_info"]["width"] == 1920

    def test_chapters(self):
        data = _build_manifest_json(
            movie_id="tt123", title="T", duration_ms=1000,
            segments=[], profiles={},
            chapters=[{"title": "Intro", "start_ms": 0, "end_ms": 5000}],
        )
        manifest = json.loads(data.decode("utf-8"))
        assert len(manifest["chapters"]) == 1
        assert manifest["chapters"][0]["title"] == "Intro"

    def test_no_video_info_or_chapters(self):
        data = _build_manifest_json(
            movie_id="tt123", title="T", duration_ms=1000,
            segments=[], profiles={},
        )
        manifest = json.loads(data.decode("utf-8"))
        assert "video_info" not in manifest
        assert "chapters" not in manifest

    def test_unicode_title(self):
        data = _build_manifest_json(
            movie_id="tt123", title="日本語タイトル", duration_ms=1000,
            segments=[], profiles={},
        )
        manifest = json.loads(data.decode("utf-8"))
        assert manifest["title"] == "日本語タイトル"


# --- _compress_manifest ---

class TestCompressManifest:
    def test_compress_decompress(self):
        original = b"This is a longer string that should compress well with zstandard. It has enough data to demonstrate that compression actually reduces size when there is meaningful content to compress."
        compressed = _compress_manifest(original)
        assert len(compressed) < len(original)
        dctx = zstandard.ZstdDecompressor()
        decompressed = dctx.decompress(compressed)
        assert decompressed == original

    def test_compression_is_deterministic(self):
        data = b"hello world"
        c1 = _compress_manifest(data)
        c2 = _compress_manifest(data)
        # zstandard level 3 with no dict should be deterministic
        assert c1 == c2


# --- _build_manifest_segments (via muxer) ---

class TestBuildManifestSegments:
    def test_basic_segment(self, muxer, sample_profiles):
        segments = [
            {"id": "seg_001", "start_time": 10.0, "end_time": 20.0, "tags": ["action"], "risk": "safe", "action": "play"},
        ]
        entries = muxer._build_manifest_segments(segments, sample_profiles, 60000)
        assert len(entries) == 1
        entry = entries[0]
        assert entry["id"] == "seg_001"
        assert entry["start_ms"] == 10000
        assert entry["end_ms"] == 20000
        assert entry["tags"] == ["action"]
        assert entry["risk"] == "safe"

    def test_profile_entries(self, muxer, sample_profiles):
        segments = [
            {"id": "seg_001", "start_time": 0.0, "end_time": 10.0, "tags": [], "risk": "safe", "action": "play"},
        ]
        entries = muxer._build_manifest_segments(segments, sample_profiles, 60000)
        assert "child" in entries[0]["profiles"]
        assert "teen" in entries[0]["profiles"]
        assert "adult" in entries[0]["profiles"]
        for pname, pentry in entries[0]["profiles"].items():
            assert pentry["action"] == "play"
            assert pentry["segment_id"] == "seg_001"

    def test_swap_action_profile_segment_id(self, muxer, sample_profiles):
        segments = [
            {"id": "seg_001", "start_time": 0.0, "end_time": 10.0, "tags": [], "risk": "mature", "action": "swap", "profile_segment_id": "filler_001"},
        ]
        entries = muxer._build_manifest_segments(segments, sample_profiles, 60000)
        for pname, pentry in entries[0]["profiles"].items():
            assert pentry["segment_id"] == "filler_001"

    def test_missing_risk_defaults_to_safe(self, muxer, sample_profiles):
        segments = [
            {"id": "seg_001", "start_time": 0.0, "end_time": 10.0, "tags": [], "action": "play"},
        ]
        entries = muxer._build_manifest_segments(segments, sample_profiles, 60000)
        assert entries[0]["risk"] == "safe"

    def test_missing_action_defaults_to_play(self, muxer, sample_profiles):
        segments = [
            {"id": "seg_001", "start_time": 0.0, "end_time": 10.0, "tags": [], "risk": "safe"},
        ]
        entries = muxer._build_manifest_segments(segments, sample_profiles, 60000)
        assert entries[0]["profiles"]["adult"]["action"] == "play"

    def test_missing_tags_defaults_to_empty(self, muxer, sample_profiles):
        segments = [
            {"id": "seg_001", "start_time": 0.0, "end_time": 10.0, "risk": "safe", "action": "play"},
        ]
        entries = muxer._build_manifest_segments(segments, sample_profiles, 60000)
        assert entries[0]["tags"] == []


# --- write_bvf / read_bvf roundtrip ---

class TestWriteBvf:
    def test_file_created(self, muxer, sample_segments, sample_profiles, tmp_path):
        out = muxer.write_bvf(tmp_path / "test.bvf", sample_segments, 360.0, sample_profiles)
        assert out.exists()
        assert out.stat().st_size > 0

    def test_returns_path(self, muxer, sample_segments, sample_profiles, tmp_path):
        out = muxer.write_bvf(tmp_path / "test.bvf", sample_segments, 360.0, sample_profiles)
        assert isinstance(out, Path)
        assert out.name == "test.bvf"

    def test_header_roundtrip(self, tmp_bvf):
        parsed = BvfMuxer.read_bvf(tmp_bvf)
        h = parsed["header"]
        assert h["magic"] == "BVF\x01\x00\x00\x00\x00"
        assert h["version_major"] == 1
        assert h["segment_count"] == 3
        assert h["total_duration_ms"] == 360000
        assert h["index_offset"] == FILE_HEADER_SIZE

    def test_index_entries(self, tmp_bvf):
        parsed = BvfMuxer.read_bvf(tmp_bvf)
        entries = parsed["segments"]
        assert len(entries) == 3
        assert entries[0]["segment_id"] == "seg_001"
        assert entries[1]["segment_id"] == "seg_002"
        assert entries[2]["segment_id"] == "seg_003"
        for entry in entries:
            assert entry["data_offset"] > FILE_HEADER_SIZE + 3 * INDEX_ENTRY_SIZE
            assert entry["data_length"] > 0
            assert entry["duration_ms"] > 0

    def test_index_entry_offsets_unique(self, tmp_bvf):
        parsed = BvfMuxer.read_bvf(tmp_bvf)
        offsets = [e["data_offset"] for e in parsed["segments"]]
        assert len(offsets) == len(set(offsets)), "All segment offsets must be unique"

    def test_manifest_content(self, tmp_bvf):
        parsed = BvfMuxer.read_bvf(tmp_bvf)
        m = parsed["manifest"]
        assert m["bvf_version"] == "1.0"
        assert m["movie_id"] == "tt1234567"
        assert m["title"] == "Test Movie"
        assert m["duration_ms"] == 360000
        assert len(m["segments"]) == 3

    def test_manifest_segment_details(self, tmp_bvf):
        parsed = BvfMuxer.read_bvf(tmp_bvf)
        segs = parsed["manifest"]["segments"]
        assert segs[0]["id"] == "seg_001"
        assert segs[0]["start_ms"] == 0
        assert segs[0]["end_ms"] == 120000
        assert segs[1]["id"] == "seg_002"
        assert segs[1]["risk"] == "mature"
        assert segs[1]["end_ms"] == 180000

    def test_manifest_profiles(self, tmp_bvf):
        parsed = BvfMuxer.read_bvf(tmp_bvf)
        segs = parsed["manifest"]["segments"]
        assert "child" in segs[0]["profiles"]
        assert "teen" in segs[0]["profiles"]
        assert "adult" in segs[0]["profiles"]

    def test_manifest_compressed(self, tmp_bvf):
        """Verify the manifest in the file is actually compressed."""
        with open(tmp_bvf, "rb") as f:
            header = f.read(FILE_HEADER_SIZE)
            h = _parse_file_header(header)
            f.seek(h["manifest_offset"])
            compressed = f.read(h["manifest_length"])
        assert len(compressed) < h["manifest_length"] * 2  # should be smaller than raw json
        dctx = zstandard.ZstdDecompressor()
        decompressed = dctx.decompress(compressed)
        json.loads(decompressed.decode("utf-8"))  # should parse

    def test_file_structure(self, tmp_bvf):
        """Verify the byte layout of the BVF file."""
        with open(tmp_bvf, "rb") as f:
            # Read header
            header = f.read(FILE_HEADER_SIZE)
            assert len(header) == FILE_HEADER_SIZE
            magic = struct.unpack("<8s", header[:8])[0]
            assert magic == FILE_MAGIC

            # Read index entries
            h = _parse_file_header(header)
            f.seek(h["index_offset"])
            for i in range(h["segment_count"]):
                entry = f.read(INDEX_ENTRY_SIZE)
                assert len(entry) == INDEX_ENTRY_SIZE
                parsed = _parse_index_entry(entry)
                assert parsed["segment_id"] in ("seg_001", "seg_002", "seg_003")

            # Read compressed manifest
            f.seek(h["manifest_offset"])
            compressed = f.read(h["manifest_length"])
            assert len(compressed) == h["manifest_length"]
            assert len(compressed) > 0

            # Verify segment blocks exist after manifest
            blocks_start = h["manifest_offset"] + h["manifest_length"]
            f.seek(blocks_start)
            block_data = f.read()
            assert len(block_data) > 0
            # Each block starts with SEG\x00
            assert block_data[:4] == BLOCK_MAGIC

    def test_file_size_reasonable(self, tmp_bvf):
        """The file should have a reasonable size (not empty, not gigabytes)."""
        size = tmp_bvf.stat().st_size
        assert size > 100  # should have header + index + manifest + blocks
        assert size < 10 * 1024 * 1024  # should be under 10MB

    def test_duration_seconds_conversion(self, muxer, sample_segments, sample_profiles, tmp_path):
        muxer.write_bvf(tmp_path / "test.bvf", sample_segments, 360.5, sample_profiles)
        parsed = BvfMuxer.read_bvf(tmp_path / "test.bvf")
        assert parsed["header"]["total_duration_ms"] == 360500

    def test_custom_codec(self, sample_segments, sample_profiles, tmp_path):
        muxer = BvfMuxer(movie_id="tt999", title="Custom Codec", codec_video=CODEC_AV1, codec_audio=CODEC_OPUS)
        out = muxer.write_bvf(tmp_path / "test.bvf", sample_segments, 100.0, sample_profiles)
        # Read the segment blocks and verify codec IDs
        parsed = BvfMuxer.read_bvf(out)
        with open(out, "rb") as f:
            blocks_start = parsed["header"]["manifest_offset"] + parsed["header"]["manifest_length"]
            f.seek(blocks_start)
            block_data = f.read()
        video_codec, audio_codec = struct.unpack("<II", block_data[20:28])
        assert video_codec == CODEC_AV1
        assert audio_codec == CODEC_OPUS

    def test_custom_flags(self, sample_segments, sample_profiles, tmp_path):
        muxer = BvfMuxer(movie_id="tt123", title="Flags", flags=FLAG_HAS_CHAPTERS)
        out = muxer.write_bvf(tmp_path / "test.bvf", sample_segments, 100.0, sample_profiles)
        parsed = BvfMuxer.read_bvf(out)
        assert parsed["header"]["flags"] == FLAG_HAS_CHAPTERS

    def test_output_path_string(self, muxer, sample_segments, sample_profiles, tmp_path):
        out = muxer.write_bvf(str(tmp_path / "test.bvf"), sample_segments, 100.0, sample_profiles)
        assert out.exists()

    def test_output_path_pathlib(self, muxer, sample_segments, sample_profiles, tmp_path):
        out = muxer.write_bvf(tmp_path / "test.bvf", sample_segments, 100.0, sample_profiles)
        assert out.exists()


# --- Empty segments ---

class TestEmptySegments:
    def test_zero_segments(self, muxer, sample_profiles, tmp_path):
        out = muxer.write_bvf(tmp_path / "empty.bvf", [], 0.0, sample_profiles)
        assert out.exists()
        parsed = BvfMuxer.read_bvf(out)
        assert parsed["header"]["segment_count"] == 0
        assert len(parsed["segments"]) == 0
        assert parsed["manifest"]["segments"] == []

    def test_empty_segments_file_structure(self, muxer, sample_profiles, tmp_path):
        out = muxer.write_bvf(tmp_path / "empty.bvf", [], 0.0, sample_profiles)
        with open(out, "rb") as f:
            header = f.read(FILE_HEADER_SIZE)
            h = _parse_file_header(header)
            assert h["segment_count"] == 0
            # Index should be empty (0 entries)
            # Manifest should follow immediately
            f.seek(h["manifest_offset"])
            compressed = f.read(h["manifest_length"])
            assert len(compressed) > 0
            # No segment blocks should follow
            f.seek(h["manifest_offset"] + h["manifest_length"])
            assert f.read() == b""


# --- Single segment ---

class TestSingleSegment:
    def test_single_segment(self, muxer, sample_profiles, tmp_path):
        segments = [
            {"id": "only_seg", "start_time": 0.0, "end_time": 60.0, "tags": ["test"], "risk": "safe", "action": "play"},
        ]
        out = muxer.write_bvf(tmp_path / "single.bvf", segments, 60.0, sample_profiles)
        parsed = BvfMuxer.read_bvf(out)
        assert parsed["header"]["segment_count"] == 1
        assert len(parsed["segments"]) == 1
        assert parsed["segments"][0]["segment_id"] == "only_seg"
        assert parsed["segments"][0]["duration_ms"] == 60000
        assert len(parsed["manifest"]["segments"]) == 1
        assert parsed["manifest"]["segments"][0]["id"] == "only_seg"


# --- Error cases ---

class TestErrorCases:
    def test_bad_risk_level(self, muxer, sample_profiles, tmp_path):
        segments = [
            {"id": "seg_001", "start_time": 0.0, "end_time": 10.0, "tags": [], "risk": "evil", "action": "play"},
        ]
        with pytest.raises(ValueError, match="Unknown risk level"):
            muxer.write_bvf(tmp_path / "bad.bvf", segments, 60.0, sample_profiles)

    def test_bad_action(self, muxer, sample_profiles, tmp_path):
        segments = [
            {"id": "seg_001", "start_time": 0.0, "end_time": 10.0, "tags": [], "risk": "safe", "action": "destroy"},
        ]
        with pytest.raises(ValueError, match="Unknown action"):
            muxer.write_bvf(tmp_path / "bad.bvf", segments, 60.0, sample_profiles)

    def test_long_segment_id(self, muxer, sample_profiles, tmp_path):
        segments = [
            {"id": "a" * 17, "start_time": 0.0, "end_time": 10.0, "tags": [], "risk": "safe", "action": "play"},
        ]
        with pytest.raises(ValueError, match="exceeds max length"):
            muxer.write_bvf(tmp_path / "bad.bvf", segments, 60.0, sample_profiles)

    def test_long_segment_id_direct(self):
        with pytest.raises(ValueError, match="exceeds max length"):
            _pad_segment_id("a" * 17)

    def test_bad_risk_direct(self):
        with pytest.raises(ValueError, match="Unknown risk level"):
            _risk_to_int("evil")

    def test_bad_action_direct(self):
        with pytest.raises(ValueError, match="Unknown action"):
            _action_to_int("destroy")


# --- _build_segment_block ---

class TestBuildSegmentBlock:
    def test_block_header_present(self):
        block = _build_segment_block(
            "seg_001",
            [{"pts_ms": 0, "data": b"\x00\x01\x02\x03"}],
            [{"pts_ms": 0, "data": b"\x10\x11"}],
            CODEC_H264,
            CODEC_AAC_LC,
        )
        magic = struct.unpack("<4s", block[:4])[0]
        assert magic == BLOCK_MAGIC
        # video packet: 16 header + 4 data = 20; audio packet: 16 header + 2 data = 18
        assert len(block) == BLOCK_HEADER_SIZE + 20 + 18

    def test_video_packets_included(self):
        block = _build_segment_block(
            "seg_001",
            [{"pts_ms": 1000, "data": b"\xde\xad"}],
            [{"pts_ms": 0, "data": b"\x00"}],
            CODEC_H264,
            CODEC_AAC_LC,
        )
        # After header, find video packet type (0x01 plus reserved bytes = 0x00000001)
        video_start = BLOCK_HEADER_SIZE
        ptype = struct.unpack("<I", block[video_start:video_start+4])[0]
        assert ptype == 0x00000001

    def test_audio_packets_included(self):
        block = _build_segment_block(
            "seg_001",
            [{"pts_ms": 0, "data": b"\x00"}],
            [{"pts_ms": 2000, "data": b"\xbe\xef"}],
            CODEC_H264,
            CODEC_AAC_LC,
        )
        # Find audio packet after video packet
        # Video packet: 4(type) + 4(size) + 8(pts) + 1(data) = 17 bytes
        audio_start = BLOCK_HEADER_SIZE + 17
        ptype = struct.unpack("<I", block[audio_start:audio_start+4])[0]
        assert ptype == 0x00000002  # PACKET_AUDIO plus reserved bytes

    def test_custom_codecs(self):
        block = _build_segment_block(
            "seg_001",
            [{"pts_ms": 0, "data": b"\x00"}],
            [{"pts_ms": 0, "data": b"\x00"}],
            CODEC_AV1,
            CODEC_OPUS,
        )
        video_codec, audio_codec = struct.unpack("<II", block[20:28])
        assert video_codec == CODEC_AV1
        assert audio_codec == CODEC_OPUS

    def test_multiple_video_packets(self):
        video_pkts = [
            {"pts_ms": 0, "data": b"\x01"},
            {"pts_ms": 100, "data": b"\x02"},
            {"pts_ms": 200, "data": b"\x03"},
        ]
        block = _build_segment_block(
            "seg_001", video_pkts, [{"pts_ms": 0, "data": b"\x00"}],
            CODEC_H264, CODEC_AAC_LC,
        )
        # After header, should have 3 video packets
        data = block[BLOCK_HEADER_SIZE:]
        # Count video packet markers
        count = 0
        offset = 0
        while offset < len(data):
            ptype = struct.unpack("<I", data[offset:offset+4])[0]
            if ptype == 0x00000001:
                count += 1
                psize = struct.unpack("<I", data[offset+4:offset+8])[0]
                pts = struct.unpack("<Q", data[offset+8:offset+16])[0]
                offset += 16 + psize
            else:
                break
        assert count == 3

    def test_pts_timestamps(self):
        block = _build_segment_block(
            "seg_001",
            [{"pts_ms": 5000, "data": b"\x00"}],
            [{"pts_ms": 5500, "data": b"\x00"}],
            CODEC_H264,
            CODEC_AAC_LC,
        )
        data = block[BLOCK_HEADER_SIZE:]
        # Video packet PTS at offset 8 within packet
        pts_video = struct.unpack("<Q", data[8:16])[0]
        assert pts_video == 5000
        # Audio packet PTS after video packet
        video_size = 16 + 1  # header(16) + data(1)
        pts_audio = struct.unpack("<Q", data[video_size + 8:video_size + 16])[0]
        assert pts_audio == 5500

    def test_empty_packets(self):
        block = _build_segment_block(
            "seg_001", [], [],
            CODEC_H264, CODEC_AAC_LC,
        )
        # Should just be the header
        assert len(block) == BLOCK_HEADER_SIZE
        magic = struct.unpack("<4s", block[:4])[0]
        assert magic == BLOCK_MAGIC

    def test_large_packet_data(self):
        large_data = bytes(range(256)) * 10  # 2560 bytes
        block = _build_segment_block(
            "seg_001",
            [{"pts_ms": 0, "data": large_data}],
            [{"pts_ms": 0, "data": b"\x00"}],
            CODEC_H264,
            CODEC_AAC_LC,
        )
        data = block[BLOCK_HEADER_SIZE:]
        psize = struct.unpack("<I", data[4:8])[0]
        assert psize == len(large_data)


# --- write_bvf with real packet data ---

class TestWriteBvfWithRealData:
    def test_roundtrip_with_real_packets(self, tmp_path):
        muxer = BvfMuxer(movie_id="tt1234567", title="Test Movie")
        segments = [
            {
                "id": "seg_001",
                "start_time": 0.0, "end_time": 120.0,
                "tags": [], "risk": "safe", "action": "play",
                "video_packets": [{"pts_ms": 0, "data": b"\x00\x01\x02\x03"}],
                "audio_packets": [{"pts_ms": 0, "data": b"\x10\x11"}],
            },
            {
                "id": "seg_002",
                "start_time": 120.0, "end_time": 180.0,
                "tags": ["violence"], "risk": "mature", "action": "swap",
                "profile_segment_id": "filler_001",
                "video_packets": [{"pts_ms": 120000, "data": b"\x04\x05\x06\x07"}],
                "audio_packets": [{"pts_ms": 120000, "data": b"\x12\x13"}],
            },
        ]
        profiles = {
            "child": {"label": "Child", "filters": ["violence"]},
            "adult": {"label": "Adult", "filters": []},
        }
        out = muxer.write_bvf(
            output_path=tmp_path / "real.bvf",
            segments=segments,
            duration_seconds=360.0,
            profiles=profiles,
        )
        parsed = BvfMuxer.read_bvf(out)
        assert parsed["header"]["segment_count"] == 2
        assert len(parsed["segments"]) == 2
        assert parsed["manifest"]["title"] == "Test Movie"
        assert parsed["manifest"]["movie_id"] == "tt1234567"

    def test_packet_data_integrity(self, tmp_path):
        """Verify that segment block data offsets and lengths are correct."""
        muxer = BvfMuxer(movie_id="tt1234567", title="Test Movie")
        video_data = b"\xde\xad\xbe\xef"
        audio_data = b"\xca\xfe\xba\xbe"
        segments = [
            {
                "id": "seg_001",
                "start_time": 0.0, "end_time": 120.0,
                "tags": [], "risk": "safe", "action": "play",
                "video_packets": [{"pts_ms": 0, "data": video_data}],
                "audio_packets": [{"pts_ms": 0, "data": audio_data}],
            },
        ]
        profiles = {"adult": {"label": "Adult", "filters": []}}
        out = muxer.write_bvf(
            output_path=tmp_path / "integrity.bvf",
            segments=segments,
            duration_seconds=120.0,
            profiles=profiles,
        )
        parsed = BvfMuxer.read_bvf(out)
        seg = parsed["segments"][0]
        assert seg["segment_id"] == "seg_001"
        assert seg["data_length"] > 0
        # Verify the block starts at the correct offset
        with open(out, "rb") as f:
            f.seek(seg["data_offset"])
            block_data = f.read(seg["data_length"])
        # First 4 bytes should be SEG\x00
        assert block_data[:4] == BLOCK_MAGIC
        # After 32-byte block header, first packet should be video (type 0x01)
        assert struct.unpack("<I", block_data[32:36])[0] == 0x00000001

    def test_mixed_stub_and_real(self, tmp_path):
        """Verify segments can mix real and stub blocks."""
        muxer = BvfMuxer(movie_id="tt1234567", title="Test Movie")
        segments = [
            {
                "id": "seg_001",
                "start_time": 0.0, "end_time": 120.0,
                "tags": [], "risk": "safe", "action": "play",
                # Real packet data
                "video_packets": [{"pts_ms": 0, "data": b"\x00\x01"}],
                "audio_packets": [{"pts_ms": 0, "data": b"\x10\x11"}],
            },
            {
                "id": "seg_002",
                "start_time": 120.0, "end_time": 180.0,
                "tags": [], "risk": "safe", "action": "play",
                # No packet data — falls back to stub
            },
            {
                "id": "seg_003",
                "start_time": 180.0, "end_time": 360.0,
                "tags": [], "risk": "safe", "action": "play",
                "video_packets": [{"pts_ms": 180000, "data": b"\x02\x03"}],
                "audio_packets": [{"pts_ms": 180000, "data": b"\x12\x13"}],
            },
        ]
        profiles = {"adult": {"label": "Adult", "filters": []}}
        out = muxer.write_bvf(
            output_path=tmp_path / "mixed.bvf",
            segments=segments,
            duration_seconds=360.0,
            profiles=profiles,
        )
        parsed = BvfMuxer.read_bvf(out)
        assert parsed["header"]["segment_count"] == 3
        # All three segments should have valid offsets
        for seg in parsed["segments"]:
            assert seg["data_offset"] > 0
            assert seg["data_length"] > 0
        # Real blocks should be larger than stub blocks
        assert parsed["segments"][0]["data_length"] > parsed["segments"][1]["data_length"]

    def test_pts_preservation(self, tmp_path):
        """Verify PTS values survive the write round-trip by checking block structure."""
        muxer = BvfMuxer(movie_id="tt1234567", title="Test Movie")
        segments = [
            {
                "id": "seg_001",
                "start_time": 0.0, "end_time": 120.0,
                "tags": [], "risk": "safe", "action": "play",
                "video_packets": [{"pts_ms": 42000, "data": b"\x00"}],
                "audio_packets": [{"pts_ms": 42500, "data": b"\x00"}],
            },
        ]
        profiles = {"adult": {"label": "Adult", "filters": []}}
        out = muxer.write_bvf(
            output_path=tmp_path / "pts.bvf",
            segments=segments,
            duration_seconds=120.0,
            profiles=profiles,
        )
        with open(out, "rb") as f:
            f.seek(0)
            header = f.read(FILE_HEADER_SIZE)
        parsed = BvfMuxer.read_bvf(out)
        seg_offset = parsed["segments"][0]["data_offset"]
        seg_length = parsed["segments"][0]["data_length"]
        with open(out, "rb") as f:
            f.seek(seg_offset)
            block = f.read(seg_length)
        # Read video packet PTS from block
        data = block[BLOCK_HEADER_SIZE:]
        pts_video = struct.unpack("<Q", data[8:16])[0]
        assert pts_video == 42000
        # Read audio packet PTS — after video packet (16 header + 1 data = 17)
        pts_audio = struct.unpack("<Q", data[17 + 8:17 + 16])[0]
        assert pts_audio == 42500
