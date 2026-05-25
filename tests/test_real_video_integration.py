"""
Integration test that runs the full analysis pipeline on a real video file.

Tests both analyzer paths for cross-verification:
  1. Whisper + Safety Checker (traditional, two-pass)
  2. Marlin-2B VLM (single-pass, unified)
  3. Profile resolution + MP4 export (for both)

Outputs are written to a persistent directory for inspection.
"""
import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest

from vid_splitter.bvf_muxer import BvfMuxer

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

ROOT = Path(__file__).resolve().parents[1]
VIDEO = ROOT / "videos" / "goldylocks.mp4"
OUTPUT_DIR = ROOT / "test_outputs" / "real_video_integration"


def _run(cmd: list[str], cwd: Path = ROOT, check: bool = True) -> subprocess.CompletedProcess[str]:
    logger.info(f"Running: {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=check)
    if result.stdout:
        logger.info(f"STDOUT:\n{result.stdout}")
    if result.stderr:
        logger.info(f"STDERR:\n{result.stderr}")
    return result


@pytest.mark.skipif(
    not VIDEO.exists(),
    reason=f"Real video not found: {VIDEO}",
)
@pytest.mark.skipif(
    subprocess.run(["which", "ffmpeg"], capture_output=True).returncode != 0
    or subprocess.run(["which", "ffprobe"], capture_output=True).returncode != 0,
    reason="ffmpeg/ffprobe are required for real-video integration test",
)
def test_whisper_safety_checker_pipeline():
    """Run Whisper + Safety Checker analysis on goldylocks.mp4."""
    out_dir = OUTPUT_DIR / "whisper"
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Output directory: {out_dir}")

    logger.info("=" * 60)
    logger.info("ANALYZER: Whisper + Safety Checker")
    logger.info("=" * 60)
    analyze = _run([
        sys.executable, "analyzer/analyze.py", str(VIDEO),
        "--model", "base",
        "--output-dir", str(out_dir),
    ])
    assert analyze.returncode == 0, f"Analysis failed:\n{analyze.stderr}"
    assert "Analysis complete" in analyze.stdout

    bvf = out_dir / "goldylocks.bvf"
    assert bvf.exists()
    parsed = BvfMuxer.read_bvf(bvf)
    assert parsed["header"]["segment_count"] >= 1
    assert parsed["header"]["total_duration_ms"] > 0
    logger.info(f"Whisper: {parsed['header']['segment_count']} segments, {parsed['header']['total_duration_ms']/1000:.1f}s")

    # Export child + adult
    child_json = ROOT / "examples" / "child_user.json"
    adult_json = ROOT / "examples" / "adult_user.json"
    child_export = out_dir / "child.mp4"
    adult_export = out_dir / "adult.mp4"
    _run([sys.executable, "tools/bvf_player.py", str(bvf), "--user-json", str(child_json), "--export", str(child_export)])
    _run([sys.executable, "tools/bvf_player.py", str(bvf), "--user-json", str(adult_json), "--export", str(adult_export)])
    assert child_export.stat().st_size > 0
    assert adult_export.stat().st_size > 0
    assert adult_export.stat().st_size >= child_export.stat().st_size
    logger.info(f"Whisper exports: child={child_export.stat().st_size/1024/1024:.1f}MB, adult={adult_export.stat().st_size/1024/1024:.1f}MB")


@pytest.mark.skipif(
    not VIDEO.exists(),
    reason=f"Real video not found: {VIDEO}",
)
@pytest.mark.skipif(
    subprocess.run(["which", "ffmpeg"], capture_output=True).returncode != 0
    or subprocess.run(["which", "ffprobe"], capture_output=True).returncode != 0,
    reason="ffmpeg/ffprobe are required for real-video integration test",
)
def test_marlin_pipeline():
    """Run Marlin-2B VLM analysis on goldylocks.mp4."""
    out_dir = OUTPUT_DIR / "marlin"
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Output directory: {out_dir}")

    logger.info("=" * 60)
    logger.info("ANALYZER: Marlin-2B VLM")
    logger.info("=" * 60)
    analyze = _run([
        sys.executable, "analyzer/marlin_analyze.py", str(VIDEO),
        "--output-dir", str(out_dir),
    ])
    assert analyze.returncode == 0, f"Analysis failed:\n{analyze.stderr}"
    assert "Analysis complete" in analyze.stdout

    bvf = out_dir / "goldylocks.bvf"
    assert bvf.exists()
    parsed = BvfMuxer.read_bvf(bvf)
    assert parsed["header"]["segment_count"] >= 1
    assert parsed["header"]["total_duration_ms"] > 0
    logger.info(f"Marlin: {parsed['header']['segment_count']} segments, {parsed['header']['total_duration_ms']/1000:.1f}s")

    # Export child + adult
    child_json = ROOT / "examples" / "child_user.json"
    adult_json = ROOT / "examples" / "adult_user.json"
    child_export = out_dir / "child.mp4"
    adult_export = out_dir / "adult.mp4"
    _run([sys.executable, "tools/bvf_player.py", str(bvf), "--user-json", str(child_json), "--export", str(child_export)])
    _run([sys.executable, "tools/bvf_player.py", str(bvf), "--user-json", str(adult_json), "--export", str(adult_export)])
    assert child_export.stat().st_size > 0
    assert adult_export.stat().st_size > 0
    assert adult_export.stat().st_size >= child_export.stat().st_size
    logger.info(f"Marlin exports: child={child_export.stat().st_size/1024/1024:.1f}MB, adult={adult_export.stat().st_size/1024/1024:.1f}MB")


def test_analyzers_agree():
    """Cross-verify that both analyzers produce valid BVF files.

    Both should produce valid BVF files with matching video duration.
    Mature content counts may differ since analyzers use different detection methods.
    """
    whisper_bvf = OUTPUT_DIR / "whisper" / "goldylocks.bvf"
    marlin_bvf = OUTPUT_DIR / "marlin" / "goldylocks.bvf"

    if not whisper_bvf.exists() or not marlin_bvf.exists():
        pytest.skip("Run other tests first to generate BVF files")

    w = BvfMuxer.read_bvf(whisper_bvf)
    m = BvfMuxer.read_bvf(marlin_bvf)

    # Durations should match (same source video)
    w_dur = w["header"]["total_duration_ms"]
    m_dur = m["header"]["total_duration_ms"]
    assert abs(w_dur - m_dur) < 1000, f"Durations differ: Whisper={w_dur}ms, Marlin={m_dur}ms"

    # Both should have at least 1 segment
    assert w["header"]["segment_count"] >= 1
    assert m["header"]["segment_count"] >= 1

    # Log mature content counts (may differ between analyzers)
    w_mature = sum(1 for s in w["manifest"]["segments"] if s["risk"] == "mature")
    m_mature = sum(1 for s in m["manifest"]["segments"] if s["risk"] == "mature")
    logger.info(f"Mature segments: Whisper={w_mature}, Marlin={m_mature}")
    logger.info(f"Cross-verification passed: both analyzers produce valid BVF files")
