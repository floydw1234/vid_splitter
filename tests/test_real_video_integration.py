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
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TESTS_DIR = ROOT / "tests"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_cli_e2e import _create_demo_video
from vid_splitter.bvf_muxer import BvfMuxer

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

VIDEO = ROOT / "videos" / "goldylocks.mp4"
OUTPUT_DIR = ROOT / "test_outputs" / "real_video_integration"
RUN_REAL_ANALYZERS = os.environ.get("RUN_REAL_ANALYZER_TESTS") == "1"


def _run(cmd: list[str], cwd: Path = ROOT, check: bool = True) -> subprocess.CompletedProcess[str]:
    logger.info(f"Running: {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=check)
    if result.stdout:
        logger.info(f"STDOUT:\n{result.stdout}")
    if result.stderr:
        logger.info(f"STDERR:\n{result.stderr}")
    return result


def _probe_json(
    bvf: Path,
    *,
    profile: str,
    verify_export: Path | None = None,
    check: bool = True,
) -> dict:
    cmd = [sys.executable, "tools/bvf_probe.py", str(bvf), "--profile", profile, "--json"]
    if verify_export is not None:
        cmd.extend(["--verify-export", str(verify_export)])
    result = _run(cmd, check=check)
    return json.loads(result.stdout)


def _assert_probe_and_exports(bvf: Path, out_dir: Path, *, child_user: Path, adult_user: Path) -> tuple[dict, dict, dict]:
    probe_payload = _probe_json(bvf, profile="child")
    assert probe_payload["valid"] is True
    assert probe_payload["segment_count"] >= 1

    child_export = out_dir / "child.mp4"
    adult_export = out_dir / "adult.mp4"
    _run([sys.executable, "tools/bvf_player.py", str(bvf), "--user-json", str(child_user), "--export", str(child_export)])
    _run([sys.executable, "tools/bvf_player.py", str(bvf), "--user-json", str(adult_user), "--export", str(adult_export)])

    assert child_export.stat().st_size > 0
    assert adult_export.stat().st_size > 0

    child_probe = _probe_json(bvf, profile="child", verify_export=child_export, check=False)
    adult_probe = _probe_json(bvf, profile="adult", verify_export=adult_export, check=False)
    assert child_probe["export_summary"]["has_video"] is True
    assert adult_probe["export_summary"]["has_video"] is True
    assert child_probe["resolved_profile"] == "child"
    assert adult_probe["resolved_profile"] == "adult"
    assert child_probe["export_summary"]["duration_ms"] > 0
    assert adult_probe["export_summary"]["duration_ms"] > 0
    return probe_payload, child_probe, adult_probe


@pytest.mark.skipif(
    subprocess.run(["which", "ffmpeg"], capture_output=True).returncode != 0
    or subprocess.run(["which", "ffprobe"], capture_output=True).returncode != 0,
    reason="ffmpeg/ffprobe are required for real-video integration test",
)
def test_generated_short_video_demo_branch_validation_workflow(tmp_path: Path):
    """CI-safe validation workflow: analyze generated video, probe BVF, export and re-probe outputs."""
    video = tmp_path / "fixture.mp4"
    _create_demo_video(video, duration=5, frequency=440)

    analyze = _run([
        sys.executable, "analyzer/analyze.py", str(video),
        "--demo-branch",
        "--output-dir", str(tmp_path),
    ])
    assert "BVF:" in analyze.stdout

    bvf = tmp_path / "fixture.bvf"
    assert bvf.exists()
    parsed = BvfMuxer.read_bvf(bvf)
    assert parsed["header"]["segment_count"] == 3

    child_json = ROOT / "examples" / "child_user.json"
    adult_json = ROOT / "examples" / "adult_user.json"
    probe_payload, child_probe, adult_probe = _assert_probe_and_exports(
        bvf,
        tmp_path,
        child_user=child_json,
        adult_user=adult_json,
    )

    assert probe_payload["keyframe_summary"]["misaligned_assets"] == 0
    assert child_probe["resolved_profile"] == "child"
    assert adult_probe["resolved_profile"] == "adult"
    assert child_probe["export_summary"]["duration_ms"] <= adult_probe["export_summary"]["duration_ms"]


@pytest.mark.skipif(
    not RUN_REAL_ANALYZERS,
    reason="set RUN_REAL_ANALYZER_TESTS=1 to run heavyweight real analyzer tests",
)
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
    """Run Whisper + Safety Checker analysis and validate the produced BVF + exports."""
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

    child_json = ROOT / "examples" / "child_user.json"
    adult_json = ROOT / "examples" / "adult_user.json"
    _, child_probe, adult_probe = _assert_probe_and_exports(
        bvf,
        out_dir,
        child_user=child_json,
        adult_user=adult_json,
    )
    assert adult_probe["export_summary"]["duration_ms"] >= child_probe["export_summary"]["duration_ms"]


@pytest.mark.skipif(
    not RUN_REAL_ANALYZERS,
    reason="set RUN_REAL_ANALYZER_TESTS=1 to run heavyweight real analyzer tests",
)
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
    """Run Marlin-2B VLM analysis and validate the produced BVF + exports."""
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

    child_json = ROOT / "examples" / "child_user.json"
    adult_json = ROOT / "examples" / "adult_user.json"
    _, child_probe, adult_probe = _assert_probe_and_exports(
        bvf,
        out_dir,
        child_user=child_json,
        adult_user=adult_json,
    )
    assert adult_probe["export_summary"]["duration_ms"] >= child_probe["export_summary"]["duration_ms"]


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
