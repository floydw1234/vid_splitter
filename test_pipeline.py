"""
End-to-End Test Script
Validates the full pipeline without needing a real video file.

Tests:
  1. Analyzer manifest generation (with a synthetic manifest)
  2. Manifest file I/O (save/load round-trip)
  3. Segment merging logic (gaps, overlaps, multi-tag)

Usage:
  python test_pipeline.py
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

# ─── Test 1: Analyzer Manifest Generation ─────────────────────────────

def test_analyzer_manifest():
    """Test that the analyzer produces a valid manifest structure."""
    print("🧪 Test 1: Analyzer manifest generation")

    try:
        import ffmpeg
        import whisper
    except ImportError:
        print("  ⏭️  Skipped (ffmpeg/whisper not installed — run pip install first)")
        return

    sys.path.insert(0, str(Path(__file__).parent / "analyzer"))
    from analyze import MovieAnalyzer, DEFAULT_PROFILES, TAG_TO_FILTER

    mock_video = Path("/tmp/test_movie.mp4")

    with patch.object(MovieAnalyzer, '__init__', lambda self, *a, **kw: None):
        analyzer = MovieAnalyzer()
        analyzer.video_path = mock_video
        analyzer.output_dir = Path("/tmp")
        analyzer.frame_interval = 5

        # Test 1a: Empty detections → single safe segment
        detections = []
        segments = analyzer._merge_segments(detections, 60.0)
        assert len(segments) == 1
        assert segments[0]["risk"] == "safe"
        assert segments[0]["action"] == "play"
        assert segments[0]["start_time"] == 0
        assert segments[0]["end_time"] == 60.0
        print("  ✅ Empty detections → single safe segment")

        # Test 1b: With a maturity detection → splits into safe + mature
        detections = [{
            "time": 25,
            "type": "audio",
            "tags": ["language"],
            "score": 1.0,
        }]
        segments = analyzer._merge_segments(detections, 60.0)
        mature = [s for s in segments if s["risk"] == "mature"]
        safe = [s for s in segments if s["risk"] == "safe"]
        assert len(mature) >= 1, f"Expected mature segments, got: {segments}"
        assert len(safe) >= 1, f"Expected safe segments, got: {segments}"
        print(f"  ✅ With detection → {len(mature)} mature, {len(safe)} safe segments")

        # Test 1c: Multiple detections close together → merged into one segment
        detections = [
            {"time": 10, "type": "audio", "tags": ["language"], "score": 1.0},
            {"time": 15, "type": "visual", "tags": ["nudity"], "score": 0.8},
            {"time": 20, "type": "audio", "tags": ["language"], "score": 1.0},
        ]
        segments = analyzer._merge_segments(detections, 60.0)
        # All three should merge into one mature segment (within 5s interval)
        mature = [s for s in segments if s["risk"] == "mature"]
        assert len(mature) == 1, f"Expected 1 merged mature segment, got {len(mature)}: {segments}"
        tags = set()
        for s in mature:
            tags.update(s["tags"])
        assert "language" in tags
        assert "nudity" in tags
        print("  ✅ Close detections merged into single segment with combined tags")

        # Test 1d: Gap filling — detections don't cover full duration
        detections = [
            {"time": 25, "type": "audio", "tags": ["language"], "score": 1.0},
        ]
        segments = analyzer._merge_segments(detections, 120.0)
        total_coverage = sum(s["end_time"] - s["start_time"] for s in segments)
        assert abs(total_coverage - 120.0) < 1.0, f"Gap filling failed: coverage={total_coverage}"
        print("  ✅ Gap filling works — full duration covered")

        # Test 1e: Manifest structure
        manifest = analyzer._build_manifest(segments, 60.0)
        assert "movie_id" in manifest
        assert "duration_seconds" in manifest
        assert "profiles" in manifest
        assert "segments" in manifest
        assert "analyzed_at" in manifest
        assert len(manifest["segments"]) > 0
        print("  ✅ Manifest has all required fields")

    print("✅ Test 1 passed\n")


# ─── Test 2: Manifest File I/O ────────────────────────────────────────

def test_manifest_io():
    """Test saving and loading a manifest file."""
    print("🧪 Test 2: Manifest file I/O")

    try:
        import ffmpeg
        import whisper
    except ImportError:
        print("  ⏭️  Skipped (deps not installed)")
        return

    sys.path.insert(0, str(Path(__file__).parent / "analyzer"))
    from analyze import MovieAnalyzer

    mock_video = Path("/tmp/test_movie.mp4")

    with patch.object(MovieAnalyzer, '__init__', lambda self, *a, **kw: None):
        analyzer = MovieAnalyzer()
        analyzer.video_path = mock_video
        analyzer.output_dir = Path("/tmp")
        analyzer.frame_interval = 5

        segments = [{
            "id": "seg_001",
            "start_time": 0.0,
            "end_time": 60.0,
            "tags": [],
            "risk": "safe",
            "action": "play",
        }]

        manifest = analyzer._build_manifest(segments, 60.0)
        output_path = analyzer._save_manifest(manifest)

        assert output_path.exists(), f"Manifest file not created: {output_path}"

        # Load it back
        with open(output_path) as f:
            loaded = json.load(f)

        assert loaded["movie_id"] == "test_movie"
        assert loaded["duration_seconds"] == 60.0
        assert len(loaded["segments"]) == 1
        assert loaded["segments"][0]["risk"] == "safe"
        print("  ✅ Manifest saved and loaded correctly")

        # Cleanup
        output_path.unlink()
        print("  ✅ Cleanup successful")

    print("✅ Test 2 passed\n")


# ─── Test 3: Segment Merging Edge Cases ───────────────────────────────

def test_segment_merging_edge_cases():
    """Test edge cases in segment merging."""
    print("🧪 Test 3: Segment merging edge cases")

    try:
        import ffmpeg
        import whisper
    except ImportError:
        print("  ⏭️  Skipped (deps not installed)")
        return

    sys.path.insert(0, str(Path(__file__).parent / "analyzer"))
    from analyze import MovieAnalyzer

    mock_video = Path("/tmp/test.mp4")

    with patch.object(MovieAnalyzer, '__init__', lambda self, *a, **kw: None):
        analyzer = MovieAnalyzer()
        analyzer.video_path = mock_video
        analyzer.output_dir = Path("/tmp")
        analyzer.frame_interval = 5

        # Edge case 1: Detection at exact start
        detections = [{"time": 0, "type": "audio", "tags": ["language"], "score": 1.0}]
        segments = analyzer._merge_segments(detections, 60.0)
        assert segments[0]["risk"] == "mature"
        assert segments[0]["start_time"] == 0
        print("  ✅ Detection at t=0 handled correctly")

        # Edge case 2: Detection at exact end
        detections = [{"time": 55, "type": "audio", "tags": ["language"], "score": 1.0}]
        segments = analyzer._merge_segments(detections, 60.0)
        assert any(s["risk"] == "mature" for s in segments)
        last_seg = segments[-1]
        assert last_seg["end_time"] == 60.0
        print("  ✅ Detection near end handled correctly")

        # Edge case 3: Many small gaps between detections
        detections = [
            {"time": i * 6, "type": "audio", "tags": ["language"], "score": 1.0}
            for i in range(10)  # Every 6s, so gaps of 1s between segments
        ]
        segments = analyzer._merge_segments(detections, 60.0)
        # Each detection creates its own segment (6s apart > 5s interval)
        # Plus gap fillers between them
        total = sum(s["end_time"] - s["start_time"] for s in segments)
        assert abs(total - 60.0) < 2.0, f"Coverage wrong: {total}"
        print(f"  ✅ Many detections → {len(segments)} segments, full coverage")

    print("✅ Test 3 passed\n")


# ─── Main ─────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Smart Branching Pipeline Tests")
    print("=" * 60 + "\n")

    tests = [
        test_analyzer_manifest,
        test_manifest_io,
        test_segment_merging_edge_cases,
    ]

    passed = 0
    failed = 0
    skipped = 0

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"  ❌ FAILED: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
        except Exception as e:
            print(f"  ❌ ERROR: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
