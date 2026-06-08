import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from analyzer.filler import (
    FillerSelection,
    build_ffmpeg_extract_command,
    extract_filler_clip,
    pick_filler_window,
)


def test_pick_filler_window_is_deterministic_for_same_seed():
    first = pick_filler_window(duration=60.0, desired_length=5.0, seed=123)
    second = pick_filler_window(duration=60.0, desired_length=5.0, seed=123)

    assert first == second
    assert first.length == pytest.approx(5.0)
    assert 0.0 <= first.start <= first.end <= 60.0


def test_pick_filler_window_avoids_forbidden_intervals():
    selection = pick_filler_window(
        duration=30.0,
        desired_length=5.0,
        seed=7,
        avoided_intervals=[
            (0.0, 10.0),
            (12.0, 17.0),
            (22.0, 30.0),
        ],
    )

    assert selection == FillerSelection(start=17.0, end=22.0, length=5.0)


def test_pick_filler_window_clamps_to_short_video_length():
    selection = pick_filler_window(duration=3.5, desired_length=5.0, seed=99)

    assert selection == FillerSelection(start=0.0, end=3.5, length=3.5)


def test_pick_filler_window_raises_when_no_valid_gap_exists():
    with pytest.raises(ValueError, match="No filler window"):
        pick_filler_window(
            duration=10.0,
            desired_length=4.0,
            seed=1,
            avoided_intervals=[(0.0, 7.0), (7.5, 10.0)],
        )


def test_build_ffmpeg_extract_command_uses_selected_window(tmp_path):
    source = tmp_path / "source.mp4"
    output = tmp_path / "filler.mp4"
    selection = FillerSelection(start=12.25, end=17.25, length=5.0)

    command = build_ffmpeg_extract_command(source, output, selection)

    assert command == [
        "ffmpeg",
        "-y",
        "-ss",
        "12.250",
        "-i",
        str(source),
        "-t",
        "5.000",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(output),
    ]


def test_extract_filler_clip_invokes_ffmpeg_with_expected_command(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    output = tmp_path / "filler.mp4"
    source.write_bytes(b"video")

    calls = []

    def fake_run(cmd, check, capture_output):
        calls.append((cmd, check, capture_output))
        output.write_bytes(b"clip")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr("analyzer.filler.subprocess.run", fake_run)

    selection = extract_filler_clip(
        source_path=source,
        output_path=output,
        duration=20.0,
        desired_length=5.0,
        seed=11,
    )

    assert len(calls) == 1
    assert calls[0][1:] == (True, True)
    assert calls[0][0] == build_ffmpeg_extract_command(source, output, selection)
    assert output.read_bytes() == b"clip"


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe are required for real filler extraction test",
)
def test_extract_filler_clip_real_video(tmp_path):
    root = Path(__file__).resolve().parents[1]
    source = root / "videos" / "goldylocks.mp4"
    if not source.exists():
        pytest.skip(f"Sample video not found: {source}")

    output = tmp_path / "filler.mp4"
    selection = extract_filler_clip(
        source_path=source,
        output_path=output,
        duration=30.0,
        desired_length=2.0,
        seed=123,
        avoided_intervals=[(0.0, 2.0)],
    )

    assert output.exists()
    assert output.stat().st_size > 0
    assert selection.length == pytest.approx(2.0)


def test_cli_runs_extraction_and_prints_selection(tmp_path, monkeypatch, capsys):
    source = tmp_path / "source.mp4"
    output = tmp_path / "filler.mp4"
    source.write_bytes(b"video")

    expected = FillerSelection(start=4.0, end=9.0, length=5.0)

    def fake_extract(**kwargs):
        assert kwargs["source_path"] == source
        assert kwargs["output_path"] == output
        assert kwargs["duration"] == 20.0
        assert kwargs["desired_length"] == 5.0
        assert kwargs["seed"] == 123
        assert kwargs["avoided_intervals"] == [(1.0, 2.0), (3.0, 4.0)]
        output.write_bytes(b"clip")
        return expected

    monkeypatch.setattr("analyzer.filler.extract_filler_clip", fake_extract)

    from analyzer import filler

    exit_code = filler.main(
        [
            str(source),
            "--duration",
            "20",
            "--seed",
            "123",
            "--output",
            str(output),
            "--avoid",
            "1:2",
            "--avoid",
            "3:4",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "start=4.000" in captured.out
    assert "end=9.000" in captured.out
    assert str(output) in captured.out
