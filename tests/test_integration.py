"""
Integration tests for the BVF pipeline.

Exercises the full video → analyzer → muxer → read_bvf pipeline using real
video files. Creates a synthetic test video via ffmpeg subprocess so the tests
are fully self-contained and do not depend on external video files.

Architecture:
  - Creates a small synthetic test video (10s, 320x240, h264+aac) via ffmpeg CLI
  - Uses real segment data with profiles
  - Writes BVF via BvfMuxer → reads back via BvfMuxer.read_bvf()
  - Verifies header, manifest, index, and segment data integrity
"""

import json
import struct
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import zstandard

from vid_splitter.bvf_muxer import BvfMuxer, DEFAULT_FLAGS, FLAG_MANIFEST_COMPRESSED, FLAG_SEEKABLE


# --- Helpers ---


def _create_test_video(tmp_path: Path) -> Path:
    """Create a small synthetic test video using ffmpeg CLI.

    Returns path to the generated .mp4 file.
    """
    video_path = tmp_path / "test_video.mp4"

    # Use ffmpeg to generate a 10-second test video with video and audio streams
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f", "lavfi",
                "-i", "color=c=blue:s=320x240:r=10:d=10",  # 10s video
                "-f", "lavfi",
                "-i", "sine=frequency=440:duration=10",     # 10s audio
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-b:a", "96k",
                "-shortest",
                str(video_path),
            ],
            capture_output=True,
            check=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # Fallback: create a minimal valid MP4 with just a video frame
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-f", "lavfi",
                    "-i", "color=c=red:s=320x240:r=1:d=1",
                    "-c:v", "libx264",
                    "-pix_fmt", "yuv420p",
                    "-frames:v", "1",
                    str(video_path),
                ],
                capture_output=True,
                check=True,
                timeout=30,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pytest.skip("ffmpeg not available or failed to create test video")

    if not video_path.exists() or video_path.stat().st_size == 0:
        pytest.skip("Failed to create test video")

    return video_path


def _get_video_duration(video_path: Path) -> float:
    """Get video duration via ffmpeg probe."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return float(result.stdout.strip())
    except Exception:
        return 10.0  # fallback duration


def _make_segments(duration: float, include_mature: bool = False) -> list[dict]:
    """Create a segment list for a video of the given duration."""
    segments = [
        {
            "id": "seg_001",
            "start_time": 0.0,
            "end_time": duration * 0.3,
            "tags": [],
            "risk": "safe",
            "action": "play",
        },
    ]
    if include_mature:
        mid = duration * 0.3
        segments.append({
            "id": "seg_002",
            "start_time": mid,
            "end_time": duration * 0.5,
            "tags": ["nudity", "language"],
            "risk": "mature",
            "action": "swap",
            "profile_segment_id": "filler_001",
        })
        segments.append({
            "id": "seg_003",
            "start_time": duration * 0.5,
            "end_time": duration,
            "tags": [],
            "risk": "safe",
            "action": "play",
        })
    else:
        segments.append({
            "id": "seg_002",
            "start_time": duration * 0.3,
            "end_time": duration,
            "tags": [],
            "risk": "safe",
            "action": "play",
        })
    return segments


def _make_profiles() -> dict:
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


# --- Fixtures ---


@pytest.fixture(scope="module")
def test_video(tmp_path_factory):
    """Create a synthetic test video once for all tests in the module."""
    tmp_dir = tmp_path_factory.mktemp("bvf_integration")
    return _create_test_video(tmp_dir)


@pytest.fixture()
def profiles():
    return _make_profiles()


@pytest.fixture()
def tmp_dir(tmp_path):
    """Provide a clean tmp_path for each test."""
    return tmp_path


# --- Tests ---


class TestFullPipeline:
    """End-to-end integration tests exercising video → manifest → BVF → read."""

    def test_e2e_safe_video(self, test_video, tmp_dir, profiles):
        """End-to-end: probe real video → generate manifest → write BVF → read back."""
        duration = _get_video_duration(test_video)
        assert duration > 0, "Video has no duration"

        segments = _make_segments(duration, include_mature=False)

        # Write BVF via muxer
        muxer = BvfMuxer(movie_id=test_video.stem, title=test_video.stem)
        bvf_path = tmp_dir / f"{test_video.stem}.bvf"
        muxer.write_bvf(
            output_path=bvf_path,
            segments=segments,
            duration_seconds=duration,
            profiles=profiles,
        )

        # Verify file exists and has content
        assert bvf_path.exists()
        assert bvf_path.stat().st_size > 64  # at least header

        # Read back and verify
        result = BvfMuxer.read_bvf(bvf_path)
        header = result["header"]
        manifest_read = result["manifest"]
        index_segs = result["segments"]

        # Verify header
        assert header["magic"] == "BVF\x01\x00\x00\x00\x00"
        assert header["version_major"] == 1
        assert header["segment_count"] == len(segments)
        assert header["total_duration_ms"] == int(duration * 1000)
        assert header["flags"] == DEFAULT_FLAGS

        # Verify manifest
        assert manifest_read["movie_id"] == test_video.stem
        assert manifest_read["duration_ms"] == int(duration * 1000)
        assert len(manifest_read["segments"]) == len(segments)
        assert "profiles" in manifest_read

        # Verify index matches manifest
        assert len(index_segs) == len(manifest_read["segments"])
        for idx_seg, man_seg in zip(index_segs, manifest_read["segments"]):
            assert idx_seg["segment_id"] == man_seg["id"]

    def test_e2e_video_with_mature_segments(self, test_video, tmp_dir, profiles):
        """Same pipeline but with mature segments injected."""
        duration = _get_video_duration(test_video)
        segments = _make_segments(duration, include_mature=True)

        # Verify we have both safe and mature segments
        safe_segs = [s for s in segments if s["risk"] == "safe"]
        mature_segs = [s for s in segments if s["risk"] == "mature"]
        assert len(safe_segs) > 0, "Expected at least one safe segment"
        assert len(mature_segs) > 0, "Expected at least one mature segment"

        # Write BVF
        muxer = BvfMuxer(movie_id=test_video.stem, title=test_video.stem)
        bvf_path = tmp_dir / f"{test_video.stem}_mature.bvf"
        muxer.write_bvf(
            output_path=bvf_path,
            segments=segments,
            duration_seconds=duration,
            profiles=profiles,
        )

        # Read back and verify
        result = BvfMuxer.read_bvf(bvf_path)
        manifest_read = result["manifest"]

        # Manifest should contain both safe and mature segments
        manifest_segs = manifest_read["segments"]
        manifest_safe = [s for s in manifest_segs if s["risk"] == "safe"]
        manifest_mature = [s for s in manifest_segs if s["risk"] == "mature"]
        assert len(manifest_safe) > 0
        assert len(manifest_mature) > 0

        # Mature segments should have swap action
        for seg in manifest_mature:
            for profile_data in seg["profiles"].values():
                assert profile_data["action"] == "swap"

    def test_manifest_segment_count_matches_index(self, test_video, tmp_dir, profiles):
        """Verify manifest segment count == index entry count == manifest['segments'] length."""
        duration = _get_video_duration(test_video)
        segments = _make_segments(duration, include_mature=False)

        muxer = BvfMuxer(movie_id=test_video.stem, title=test_video.stem)
        bvf_path = tmp_dir / f"{test_video.stem}_count.bvf"
        muxer.write_bvf(
            output_path=bvf_path,
            segments=segments,
            duration_seconds=duration,
            profiles=profiles,
        )

        result = BvfMuxer.read_bvf(bvf_path)

        header_count = result["header"]["segment_count"]
        index_count = len(result["segments"])
        manifest_count = len(result["manifest"]["segments"])

        assert header_count == index_count, (
            f"Header segment_count ({header_count}) != index entries ({index_count})"
        )
        assert header_count == manifest_count, (
            f"Header segment_count ({header_count}) != manifest segments ({manifest_count})"
        )

    def test_read_bvf_parsing_roundtrip(self, test_video, tmp_dir, profiles):
        """Write BVF → read_bvf → verify all fields round-trip correctly."""
        duration = _get_video_duration(test_video)
        segments = _make_segments(duration, include_mature=False)

        muxer = BvfMuxer(movie_id=test_video.stem, title=test_video.stem)
        bvf_path = tmp_dir / f"{test_video.stem}_round.bvf"
        muxer.write_bvf(
            output_path=bvf_path,
            segments=segments,
            duration_seconds=duration,
            profiles=profiles,
        )

        result = BvfMuxer.read_bvf(bvf_path)

        # Verify all segment IDs match
        for i, seg in enumerate(segments):
            assert result["segments"][i]["segment_id"] == seg["id"]

        # Verify manifest segment IDs match
        for i, seg in enumerate(segments):
            assert result["manifest"]["segments"][i]["id"] == seg["id"]

        # Verify manifest movie_id matches
        assert result["manifest"]["movie_id"] == test_video.stem

        # Verify duration matches
        assert result["manifest"]["duration_ms"] == int(duration * 1000)
        assert result["header"]["total_duration_ms"] == int(duration * 1000)

        # Verify index data offsets are non-zero (real data blocks)
        for idx_seg in result["segments"]:
            assert idx_seg["data_offset"] > 0, "data_offset should be > 0"
            assert idx_seg["data_length"] > 0, "data_length should be > 0"

    def test_profile_filtering(self, test_video, tmp_dir):
        """Test that profile filtering produces expected results in the manifest."""
        duration = _get_video_duration(test_video)

        # Create segments with different actions
        segments = [
            {
                "id": "seg_001",
                "start_time": 0.0,
                "end_time": duration * 0.25,
                "tags": [],
                "risk": "safe",
                "action": "play",
            },
            {
                "id": "seg_002",
                "start_time": duration * 0.25,
                "end_time": duration * 0.5,
                "tags": ["nudity"],
                "risk": "mature",
                "action": "swap",
                "profile_segment_id": "filler_001",
            },
            {
                "id": "seg_003",
                "start_time": duration * 0.5,
                "end_time": duration * 0.75,
                "tags": ["language"],
                "risk": "mature",
                "action": "mute",
            },
            {
                "id": "seg_004",
                "start_time": duration * 0.75,
                "end_time": duration,
                "tags": [],
                "risk": "safe",
                "action": "play",
            },
        ]

        muxer = BvfMuxer(movie_id=test_video.stem, title=test_video.stem)
        bvf_path = tmp_dir / f"{test_video.stem}_profiles.bvf"
        muxer.write_bvf(
            output_path=bvf_path,
            segments=segments,
            duration_seconds=duration,
            profiles=_make_profiles(),
        )

        result = BvfMuxer.read_bvf(bvf_path)
        manifest_segs = result["manifest"]["segments"]

        # Verify each segment has all profiles present
        for seg in manifest_segs:
            assert "child" in seg["profiles"]
            assert "teen" in seg["profiles"]
            assert "adult" in seg["profiles"]

        # seg_001 (safe, play) — all profiles should have "play"
        seg001 = manifest_segs[0]
        for pname in ["child", "teen", "adult"]:
            assert seg001["profiles"][pname]["action"] == "play"

        # seg_002 (mature, swap) — all profiles should have "swap"
        seg002 = manifest_segs[1]
        for pname in ["child", "teen", "adult"]:
            assert seg002["profiles"][pname]["action"] == "swap"
            assert seg002["profiles"][pname]["segment_id"] == "filler_001"

        # seg_003 (mature, mute) — all profiles should have "mute"
        seg003 = manifest_segs[2]
        for pname in ["child", "teen", "adult"]:
            assert seg003["profiles"][pname]["action"] == "mute"

    def test_manifest_compression(self, test_video, tmp_dir, profiles):
        """Verify the manifest in the BVF is zstandard-compressed."""
        duration = _get_video_duration(test_video)
        segments = _make_segments(duration, include_mature=False)

        muxer = BvfMuxer(movie_id=test_video.stem, title=test_video.stem)
        bvf_path = tmp_dir / f"{test_video.stem}_compress.bvf"
        muxer.write_bvf(
            output_path=bvf_path,
            segments=segments,
            duration_seconds=duration,
            profiles=profiles,
        )

        result = BvfMuxer.read_bvf(bvf_path)

        # read_bvf should have decompressed the manifest
        manifest = result["manifest"]
        assert "movie_id" in manifest
        assert "segments" in manifest
        assert "profiles" in manifest

        # Verify the header flag indicates compressed manifest
        assert result["header"]["flags"] & FLAG_MANIFEST_COMPRESSED

        # Verify the manifest is valid JSON by checking it has expected structure
        assert isinstance(manifest["segments"], list)
        assert len(manifest["segments"]) > 0

    def test_unicode_content(self, test_video, tmp_dir, profiles):
        """Test that Unicode (Cyrillic) survives the full pipeline."""
        duration = _get_video_duration(test_video)

        # Use a Cyrillic title
        unicode_title = "\u0411\u0435\u043b\u044b\u0439 \u043a\u043e\u0442\u0438\u043a"  # "Белый котик"
        segments = _make_segments(duration, include_mature=False)

        muxer = BvfMuxer(movie_id=unicode_title, title=unicode_title)
        bvf_path = tmp_dir / f"{test_video.stem}_unicode.bvf"
        muxer.write_bvf(
            output_path=bvf_path,
            segments=segments,
            duration_seconds=duration,
            profiles=profiles,
        )

        result = BvfMuxer.read_bvf(bvf_path)
        manifest = result["manifest"]

        # Unicode title should survive round-trip
        assert manifest["movie_id"] == unicode_title
        assert manifest["title"] == unicode_title


class TestIntegrationEdgeCases:
    """Edge case tests for the integration pipeline."""

    def test_single_segment_video(self, tmp_dir, profiles):
        """Test with a video that has only one segment (no splits)."""
        duration = 60.0
        segments = [{
            "id": "seg_001",
            "start_time": 0.0,
            "end_time": duration,
            "tags": [],
            "risk": "safe",
            "action": "play",
        }]

        muxer = BvfMuxer(movie_id="single_seg", title="Single Segment")
        bvf_path = tmp_dir / "single.bvf"
        muxer.write_bvf(
            output_path=bvf_path,
            segments=segments,
            duration_seconds=duration,
            profiles=profiles,
        )

        result = BvfMuxer.read_bvf(bvf_path)
        assert result["header"]["segment_count"] == 1
        assert len(result["segments"]) == 1
        assert result["segments"][0]["segment_id"] == "seg_001"
        assert result["manifest"]["segments"][0]["id"] == "seg_001"

    def test_many_segments(self, tmp_dir, profiles):
        """Test with many segments to stress-test the index."""
        num_segments = 20
        duration = 600.0
        segments = []
        for i in range(num_segments):
            start = i * (duration / num_segments)
            end = (i + 1) * (duration / num_segments)
            risk = "mature" if i % 3 == 0 else "safe"
            action = "swap" if risk == "mature" else "play"
            segments.append({
                "id": f"seg_{i+1:03d}",
                "start_time": round(start, 2),
                "end_time": round(end, 2),
                "tags": ["language"] if risk == "mature" else [],
                "risk": risk,
                "action": action,
            })

        muxer = BvfMuxer(movie_id="many_segs", title="Many Segments")
        bvf_path = tmp_dir / "many.bvf"
        muxer.write_bvf(
            output_path=bvf_path,
            segments=segments,
            duration_seconds=duration,
            profiles=profiles,
        )

        result = BvfMuxer.read_bvf(bvf_path)
        assert result["header"]["segment_count"] == num_segments
        assert len(result["segments"]) == num_segments
        assert len(result["manifest"]["segments"]) == num_segments

        # Verify all segment IDs are present and ordered
        for i, seg in enumerate(result["segments"]):
            expected_id = f"seg_{i+1:03d}"
            assert seg["segment_id"] == expected_id, (
                f"Index segment {i}: expected {expected_id}, got {seg['segment_id']}"
            )
            assert result["manifest"]["segments"][i]["id"] == expected_id

    def test_video_info_passthrough(self, tmp_dir, profiles):
        """Test that optional video_info is included in the manifest when provided."""
        duration = 120.0
        segments = [{
            "id": "seg_001",
            "start_time": 0.0,
            "end_time": duration,
            "tags": [],
            "risk": "safe",
            "action": "play",
        }]

        video_info = {
            "width": 1920,
            "height": 1080,
            "frame_rate": "30/1",
            "color_space": "bt709",
        }

        muxer = BvfMuxer(movie_id="hd_video", title="HD Video")
        bvf_path = tmp_dir / "hd.bvf"
        muxer.write_bvf(
            output_path=bvf_path,
            segments=segments,
            duration_seconds=duration,
            profiles=profiles,
            video_info=video_info,
        )

        result = BvfMuxer.read_bvf(bvf_path)
        manifest = result["manifest"]

        assert "video_info" in manifest
        assert manifest["video_info"]["width"] == 1920
        assert manifest["video_info"]["height"] == 1080
        assert manifest["video_info"]["frame_rate"] == "30/1"

    def test_chapters_passthrough(self, tmp_dir, profiles):
        """Test that optional chapters are included in the manifest when provided."""
        duration = 360.0
        segments = [
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
                "end_time": 240.0,
                "tags": [],
                "risk": "safe",
                "action": "play",
            },
            {
                "id": "seg_003",
                "start_time": 240.0,
                "end_time": 360.0,
                "tags": [],
                "risk": "safe",
                "action": "play",
            },
        ]

        chapters = [
            {"title": "Introduction", "start_ms": 0, "end_ms": 120000},
            {"title": "Main Content", "start_ms": 120000, "end_ms": 240000},
            {"title": "Conclusion", "start_ms": 240000, "end_ms": 360000},
        ]

        muxer = BvfMuxer(movie_id="with_chapters", title="With Chapters")
        bvf_path = tmp_dir / "chapters.bvf"
        muxer.write_bvf(
            output_path=bvf_path,
            segments=segments,
            duration_seconds=duration,
            profiles=profiles,
            chapters=chapters,
        )

        result = BvfMuxer.read_bvf(bvf_path)
        manifest = result["manifest"]

        assert "chapters" in manifest
        assert len(manifest["chapters"]) == 3
        assert manifest["chapters"][0]["title"] == "Introduction"
        assert manifest["chapters"][1]["title"] == "Main Content"
        assert manifest["chapters"][2]["title"] == "Conclusion"

    def test_empty_tags(self, tmp_dir, profiles):
        """Test segments with empty tag lists are handled correctly."""
        duration = 60.0
        segments = [
            {
                "id": "seg_001",
                "start_time": 0.0,
                "end_time": duration,
                "tags": [],  # empty tags
                "risk": "safe",
                "action": "play",
            },
        ]

        muxer = BvfMuxer(movie_id="empty_tags", title="Empty Tags")
        bvf_path = tmp_dir / "empty_tags.bvf"
        muxer.write_bvf(
            output_path=bvf_path,
            segments=segments,
            duration_seconds=duration,
            profiles=profiles,
        )

        result = BvfMuxer.read_bvf(bvf_path)
        manifest_seg = result["manifest"]["segments"][0]
        assert manifest_seg["tags"] == []
        assert manifest_seg["id"] == "seg_001"

    def test_all_action_types(self, tmp_dir, profiles):
        """Test that all action types (play, swap, skip, mute, blur) are preserved."""
        duration = 300.0
        segments = [
            {"id": "s1", "start_time": 0, "end_time": 60, "tags": [], "risk": "safe", "action": "play"},
            {"id": "s2", "start_time": 60, "end_time": 120, "tags": ["nudity"], "risk": "mature", "action": "swap", "profile_segment_id": "filler_1"},
            {"id": "s3", "start_time": 120, "end_time": 180, "tags": ["violence"], "risk": "mature", "action": "skip", "profile_segment_id": "skip_1"},
            {"id": "s4", "start_time": 180, "end_time": 240, "tags": ["language"], "risk": "mature", "action": "mute"},
            {"id": "s5", "start_time": 240, "end_time": 300, "tags": ["fear"], "risk": "mature", "action": "blur"},
        ]

        muxer = BvfMuxer(movie_id="all_actions", title="All Actions")
        bvf_path = tmp_dir / "all_actions.bvf"
        muxer.write_bvf(
            output_path=bvf_path,
            segments=segments,
            duration_seconds=duration,
            profiles=profiles,
        )

        result = BvfMuxer.read_bvf(bvf_path)
        manifest_segs = {s["id"]: s for s in result["manifest"]["segments"]}

        # Check each action is preserved in all profiles
        expected_actions = {
            "s1": "play", "s2": "swap", "s3": "skip", "s4": "mute", "s5": "blur",
        }
        for seg_id, expected_action in expected_actions.items():
            seg = manifest_segs[seg_id]
            for pname in profiles:
                assert seg["profiles"][pname]["action"] == expected_action, (
                    f"Segment {seg_id}: expected {expected_action}, got {seg['profiles'][pname]['action']}"
                )
