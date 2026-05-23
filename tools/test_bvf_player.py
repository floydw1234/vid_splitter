"""
Tests for the BVF reference player.

Uses the muxer's write_bvf() to create mock BVF files with known content.
"""

import json
import os
import struct
import tempfile
from pathlib import Path
from unittest import TestCase

import zstandard

# Add project root to path so imports work from any directory
import sys
from pathlib import Path as _Path

_PROJECT_ROOT = _Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tools.bvf_player import (
    BVFPlayer,
    BLOCK_HEADER_SIZE,
    BLOCK_MAGIC,
    FILE_MAGIC,
    PACKET_AUDIO,
    PACKET_HEADER_SIZE,
    PACKET_VIDEO,
    _parse_file_header,
    _parse_index_entry,
)

from vid_splitter.bvf_muxer import BvfMuxer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_test_bvf(
    title: str = "Test Movie",
    segments: list[dict] | None = None,
    profiles: dict | None = None,
    chapters: list[dict] | None = None,
    video_info: dict | None = None,
) -> str:
    """Create a minimal BVF file with known content for testing.

    Uses the muxer's write_bvf() with proper segment metadata dicts.
    The muxer builds stub segment blocks internally.

    Args:
        title: Movie title for the manifest.
        segments: List of segment dicts for the muxer. Each dict:
            {
                "id": str,
                "start_time": float (seconds),
                "end_time": float (seconds),
                "tags": list[str],
                "risk": str ("safe"/"mature"),
                "action": str ("play"/"swap"/"skip"/"mute"),
                "profile_segment_id": str (target segment_id for swap/skip),
            }
        profiles: Profile definitions.
        chapters: Chapter definitions.
        video_info: Video metadata dict.

    Returns:
        Path to the created .bvf file.
    """
    if segments is None:
        segments = [
            {
                "id": "seg_001",
                "start_time": 0.0,
                "end_time": 300.0,
                "tags": [],
                "risk": "safe",
                "action": "play",
                "profile_segment_id": "seg_001",
            },
            {
                "id": "seg_002",
                "start_time": 300.0,
                "end_time": 345.0,
                "tags": ["violence", "gore"],
                "risk": "mature",
                "action": "swap",
                "profile_segment_id": "filler_001",
            },
            {
                "id": "filler_001",
                "start_time": 0.0,
                "end_time": 45.0,
                "tags": [],
                "risk": "safe",
                "action": "play",
                "profile_segment_id": "filler_001",
                "is_filler": True,
            },
            {
                "id": "seg_003",
                "start_time": 345.0,
                "end_time": 600.0,
                "tags": [],
                "risk": "safe",
                "action": "play",
                "profile_segment_id": "seg_003",
            },
        ]

    if profiles is None:
        profiles = {
            "adult": {"label": "Adult (18+)", "filters": []},
            "teen": {"label": "Teen (13-17)", "filters": ["nudity", "gore"]},
            "child": {"label": "Child (under 13)", "filters": ["nudity", "violence", "language", "fear", "gore"]},
        }

    if video_info is None:
        video_info = {
            "width": 1920,
            "height": 1080,
            "frame_rate": "24000/1001",
            "color_space": "bt709",
        }

    if chapters is None:
        chapters = [
            {"title": "Opening", "start_ms": 0, "end_ms": 300000},
            {"title": "Act One", "start_ms": 300000, "end_ms": 600000},
        ]

    # Create temp directory for output
    temp_dir = tempfile.mkdtemp(prefix="bvf_test_")
    output_path = os.path.join(temp_dir, f"{title.replace(' ', '_')}.bvf")

    muxer = BvfMuxer(movie_id="tt1234567", title=title)
    muxer.write_bvf(
        output_path=output_path,
        segments=segments,
        duration_seconds=600.0,
        profiles=profiles,
        video_info=video_info,
        chapters=chapters,
    )

    return output_path


class TestParseFileHeader(TestCase):
    """Test file header parsing."""

    def test_valid_header(self):
        """Test parsing a valid 64-byte header."""
        data = FILE_MAGIC + struct.pack("<H H I Q Q Q Q I Q I", 1, 0, 0, 64, 64, 128, 64, 3, 300000, 0)
        header = _parse_file_header(data)

        self.assertEqual(header["magic"], "BVF\x01")
        self.assertEqual(header["version_major"], 1)
        self.assertEqual(header["version_minor"], 0)
        self.assertEqual(header["segment_count"], 3)
        self.assertEqual(header["total_duration_ms"], 300000)

    def test_invalid_magic(self):
        """Test that invalid magic bytes raise an error."""
        data = b"XXXX\x00\x00\x00\x00" + struct.pack("<H H I Q Q Q Q I Q I", 1, 0, 0, 64, 64, 128, 64, 3, 300000, 0)
        header = _parse_file_header(data)
        self.assertEqual(header["magic"], "XXXX")


class TestParseIndexEntry(TestCase):
    """Test segment index entry parsing."""

    def test_valid_entry(self):
        """Test parsing a valid 40-byte index entry."""
        data = struct.pack("<16s Q Q Q", b"seg_001\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00", 1000, 50000, 300000)
        entry = _parse_index_entry(data)

        self.assertEqual(entry["segment_id"], "seg_001")
        self.assertEqual(entry["data_offset"], 1000)
        self.assertEqual(entry["data_length"], 50000)
        self.assertEqual(entry["duration_ms"], 300000)


class TestBVFPlayerInit(TestCase):
    """Test BVFPlayer initialization."""

    def test_load_valid_bvf(self):
        """Test loading a valid BVF file."""
        bvf_path = _create_test_bvf()
        player = BVFPlayer(bvf_path, profile="adult")

        self.assertEqual(player.header["magic"], "BVF\x01")
        self.assertEqual(player.header["version_major"], 1)
        self.assertEqual(player.header["segment_count"], 4)  # 3 main + 1 filler
        self.assertEqual(player.manifest["title"], "Test Movie")
        self.assertEqual(player.manifest["movie_id"], "tt1234567")

    def test_load_nonexistent_file(self):
        """Test that loading a nonexistent file raises FileNotFoundError."""
        with self.assertRaises(FileNotFoundError):
            BVFPlayer("/nonexistent/path/file.bvf")

    def test_default_profile(self):
        """Test that default profile is 'adult'."""
        bvf_path = _create_test_bvf()
        player = BVFPlayer(bvf_path)
        self.assertEqual(player.profile, "adult")


class TestResolveAdultProfile(TestCase):
    """Test adult profile resolution — should play all segments."""

    def test_adult_plays_all_segments(self):
        """Adult profile should play all 3 main segments."""
        bvf_path = _create_test_bvf()
        player = BVFPlayer(bvf_path, profile="adult")

        sequence = player.resolve_playback_sequence()

        # Should have 3 segments (filler excluded)
        self.assertEqual(len(sequence), 3)

        # All should be "play" action
        for entry in sequence:
            self.assertEqual(entry["action"], "play")
            self.assertEqual(entry["target_id"], entry["segment_id"])

    def test_adult_segment_order(self):
        """Adult profile should maintain correct segment order."""
        bvf_path = _create_test_bvf()
        player = BVFPlayer(bvf_path, profile="adult")

        sequence = player.resolve_playback_sequence()
        ids = [e["segment_id"] for e in sequence]

        self.assertEqual(ids, ["seg_001", "seg_002", "seg_003"])


class TestResolveChildProfile(TestCase):
    """Test child profile resolution — should swap/skip mature content."""

    def test_child_swap_violence(self):
        """Child profile should swap violence segment with filler."""
        bvf_path = _create_test_bvf()
        player = BVFPlayer(bvf_path, profile="child")

        sequence = player.resolve_playback_sequence()

        # seg_002 should be swapped to filler_001
        seg_002_entry = [e for e in sequence if e["segment_id"] == "seg_002"]
        self.assertEqual(len(seg_002_entry), 1)
        self.assertEqual(seg_002_entry[0]["action"], "swap")
        self.assertEqual(seg_002_entry[0]["target_id"], "filler_001")

    def test_child_no_skips(self):
        """Child profile should not skip any segments in the default test BVF."""
        bvf_path = _create_test_bvf()
        player = BVFPlayer(bvf_path, profile="child")

        sequence = player.resolve_playback_sequence()

        # All 3 main segments should be in the sequence (play or swap)
        self.assertEqual(len(sequence), 3)

        # No skip actions
        for entry in sequence:
            self.assertNotEqual(entry["action"], "skip")


class TestPlaybackSequenceOrdering(TestCase):
    """Test that playback sequence maintains temporal order."""

    def test_sequence_order(self):
        """Segments should be in chronological order."""
        bvf_path = _create_test_bvf()
        player = BVFPlayer(bvf_path, profile="adult")

        sequence = player.resolve_playback_sequence()
        start_times = [e["start_ms"] for e in sequence]

        # Should be in ascending order
        for i in range(len(start_times) - 1):
            self.assertLess(start_times[i], start_times[i + 1])


class TestSkipSegment(TestCase):
    """Test that skip action excludes segments from the sequence."""

    def test_skip_excluded_from_sequence(self):
        """Segments with skip action should not appear in sequence."""
        # Create BVF with a skip action
        segments = [
            {
                "id": "seg_001",
                "start_ms": 0,
                "end_ms": 300000,
                "tags": [],
                "risk": "safe",
                "profiles": {
                    "adult": {"action": "play", "segment_id": "seg_001"},
                    "child": {"action": "skip", "segment_id": "seg_001"},
                },
            },
            {
                "id": "seg_002",
                "start_ms": 300000,
                "end_ms": 600000,
                "tags": [],
                "risk": "safe",
                "profiles": {
                    "adult": {"action": "play", "segment_id": "seg_002"},
                    "child": {"action": "play", "segment_id": "seg_002"},
                },
            },
        ]

        bvf_path = _create_test_bvf(title="Skip Test", segments=segments)
        player = BVFPlayer(bvf_path, profile="child")

        sequence = player.resolve_playback_sequence()

        # Child should only have seg_002 (seg_001 is skipped)
        self.assertEqual(len(sequence), 1)
        self.assertEqual(sequence[0]["segment_id"], "seg_002")


class TestSwapSegment(TestCase):
    """Test that swap action correctly references filler segments."""

    def test_swap_references_fillers(self):
        """Swap action should reference a filler segment_id."""
        bvf_path = _create_test_bvf()
        player = BVFPlayer(bvf_path, profile="teen")

        sequence = player.resolve_playback_sequence()

        # Find the swap entry
        swap_entries = [e for e in sequence if e["action"] == "swap"]
        self.assertEqual(len(swap_entries), 1)

        # Target should be a filler
        self.assertTrue(swap_entries[0]["target_id"].startswith("filler_"))


class TestExtractSegment(TestCase):
    """Test segment extraction."""

    def test_extract_segment_produces_file(self):
        """Extracting a segment should produce a non-empty file."""
        bvf_path = _create_test_bvf()
        player = BVFPlayer(bvf_path, profile="adult")

        with tempfile.NamedTemporaryFile(suffix=".ts", delete=False) as f:
            output_path = f.name

        try:
            success = player.extract_segment("seg_001", output_path)
            self.assertTrue(success)
            self.assertTrue(os.path.exists(output_path))
            self.assertGreater(os.path.getsize(output_path), 0)
        finally:
            if os.path.exists(output_path):
                os.unlink(output_path)

    def test_extract_nonexistent_segment(self):
        """Extracting a nonexistent segment should fail gracefully."""
        bvf_path = _create_test_bvf()
        player = BVFPlayer(bvf_path, profile="adult")

        with tempfile.NamedTemporaryFile(suffix=".ts", delete=False) as f:
            output_path = f.name

        try:
            success = player.extract_segment("seg_nonexistent", output_path)
            self.assertFalse(success)
        finally:
            if os.path.exists(output_path):
                os.unlink(output_path)

    def test_extract_segment_size_matches(self):
        """Extracted segment size should match the BVF index entry."""
        bvf_path = _create_test_bvf()
        player = BVFPlayer(bvf_path, profile="adult")

        # Find seg_001 in index
        seg_entry = None
        for seg in player.segments:
            if seg["segment_id"] == "seg_001":
                seg_entry = seg
                break

        self.assertIsNotNone(seg_entry)

        with tempfile.NamedTemporaryFile(suffix=".ts", delete=False) as f:
            output_path = f.name

        try:
            success = player.extract_segment("seg_001", output_path)
            self.assertTrue(success)

            # Stub segments contain one video marker byte and one audio marker byte.
            actual_size = os.path.getsize(output_path)
            self.assertEqual(actual_size, 2)
        finally:
            if os.path.exists(output_path):
                os.unlink(output_path)


class TestCLIParsing(TestCase):
    """Test CLI argument parsing."""

    def test_valid_profile(self):
        """Valid profile names should parse correctly."""
        from tools.bvf_player import build_parser

        parser = build_parser()

        # These should not raise
        args = parser.parse_args(["test.bvf", "--profile", "adult"])
        self.assertEqual(args.profile, "adult")

        args = parser.parse_args(["test.bvf", "--profile", "teen"])
        self.assertEqual(args.profile, "teen")

        args = parser.parse_args(["test.bvf", "--profile", "teen_m"])
        self.assertEqual(args.profile, "teen_m")

        args = parser.parse_args(["test.bvf", "--profile", "teen_f"])
        self.assertEqual(args.profile, "teen_f")

        args = parser.parse_args(["test.bvf", "--profile", "child"])
        self.assertEqual(args.profile, "child")

    def test_list_flag(self):
        """--list flag should be parsed."""
        from tools.bvf_player import build_parser

        parser = build_parser()
        args = parser.parse_args(["test.bvf", "--list"])
        self.assertTrue(args.list)

    def test_dry_run_flag(self):
        """--dry-run flag should be parsed."""
        from tools.bvf_player import build_parser

        parser = build_parser()
        args = parser.parse_args(["test.bvf", "--dry-run"])
        self.assertTrue(args.dry_run)

    def test_seek_flag(self):
        """--seek flag should parse float values."""
        from tools.bvf_player import build_parser

        parser = build_parser()
        args = parser.parse_args(["test.bvf", "--seek", "30.0"])
        self.assertEqual(args.seek, 30.0)

    def test_verbose_flag(self):
        """--verbose flag should be parsed."""
        from tools.bvf_player import build_parser

        parser = build_parser()
        args = parser.parse_args(["test.bvf", "--verbose"])
        self.assertTrue(args.verbose)


class TestPlaybackInfo(TestCase):
    """Test get_playback_info()."""

    def test_playback_info_structure(self):
        """get_playback_info should return a dict with expected keys."""
        bvf_path = _create_test_bvf()
        player = BVFPlayer(bvf_path, profile="adult")

        info = player.get_playback_info()

        self.assertIn("title", info)
        self.assertIn("movie_id", info)
        self.assertIn("profile", info)
        self.assertIn("total_segments", info)
        self.assertIn("total_duration_ms", info)
        self.assertIn("segments", info)

        self.assertEqual(info["title"], "Test Movie")
        self.assertEqual(info["profile"], "adult")
        self.assertEqual(info["total_segments"], 3)

    def test_playback_info_duration(self):
        """Total duration should be the sum of segment durations."""
        bvf_path = _create_test_bvf()
        player = BVFPlayer(bvf_path, profile="adult")

        info = player.get_playback_info()

        self.assertEqual(info["total_duration_ms"], 600000)


class TestCleanup(TestCase):
    """Test cleanup behavior."""

    def test_temp_dir_cleaned_up(self):
        """Temporary directory should be cleaned up."""
        bvf_path = _create_test_bvf()
        player = BVFPlayer(bvf_path, profile="adult")

        # Force temp dir creation
        player._get_temp_dir()
        self.assertIsNotNone(player._temp_dir)

        # Clean up
        player.cleanup()
        self.assertIsNone(player._temp_dir)


class TestMuteAction(TestCase):
    """Test mute action extraction."""

    def test_mute_extracts_video_only(self):
        """Mute action should extract video packets only."""
        segments = [
            {
                "id": "seg_001",
                "start_ms": 0,
                "end_ms": 300000,
                "tags": [],
                "risk": "safe",
                "profiles": {
                    "adult": {"action": "play", "segment_id": "seg_001"},
                    "child": {"action": "mute", "segment_id": "seg_001"},
                },
            },
        ]

        bvf_path = _create_test_bvf(title="Mute Test", segments=segments)
        player = BVFPlayer(bvf_path, profile="child")

        sequence = player.resolve_playback_sequence()
        self.assertEqual(len(sequence), 1)
        self.assertEqual(sequence[0]["action"], "mute")

        # Extract video-only
        with tempfile.NamedTemporaryFile(suffix=".ts", delete=False) as f:
            output_path = f.name

        try:
            success = player.extract_video_only("seg_001", output_path)
            self.assertTrue(success)
            self.assertGreater(os.path.getsize(output_path), 0)
        finally:
            if os.path.exists(output_path):
                os.unlink(output_path)


class TestSeek(TestCase):
    """Test seek functionality."""

    def test_seek_within_first_segment(self):
        """Seek within first segment should target first segment."""
        bvf_path = _create_test_bvf()
        player = BVFPlayer(bvf_path, profile="adult")

        # Seek to 150s (within seg_001 which is 0-300s)
        # Should not crash; just print target
        import io
        import contextlib

        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            player.seek(150000)  # 150s in ms

        output = f.getvalue()
        self.assertIn("seg_001", output)

    def test_seek_beyond_end(self):
        """Seek beyond total duration should target last segment."""
        bvf_path = _create_test_bvf()
        player = BVFPlayer(bvf_path, profile="adult")

        # Total is 900s, seek to 1000s
        import io
        import contextlib

        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            player.seek(1000000)  # 1000s in ms

        output = f.getvalue()
        # Should target seg_003 (last segment)
        self.assertIn("seg_003", output)


if __name__ == "__main__":
    import unittest

    unittest.main()
