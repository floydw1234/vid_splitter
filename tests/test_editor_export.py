import shutil
import subprocess
from pathlib import Path

import pytest

from editor.export import (
    ReplacementInfo,
    build_export_command,
    build_filter_graph,
    expected_output_duration,
)
from editor.ffmpeg_util import ffmpeg_available, probe_video
from editor.project import Project, ReplacementEntry, Segment


def _project(**kwargs) -> Project:
    base = {
        "id": "p1",
        "name": "t",
        "source_video": "source.mp4",
        "duration": 6.0,
        "fps": 24.0,
        "width": 320,
        "height": 180,
        "has_audio": True,
    }
    base.update(kwargs)
    return Project(**base)


def test_cut_only_shortens_output():
    p = _project(
        segments=[Segment(id="c", start=3.0, end=4.0, action="cut", status="accepted")]
    )
    assert expected_output_duration(p) == 5.0
    graph, extras = build_filter_graph(p, {})
    assert "trim=start=0" in graph
    assert "trim=start=4" in graph or "end=6" in graph


def test_blur_filter_present():
    p = _project(segments=[Segment(id="b", start=1.0, end=2.0, action="blur")])
    graph, _ = build_filter_graph(p, {})
    assert "gblur=sigma=40:steps=3:enable=" in graph
    assert "split[vb" not in graph


def test_mute_filter():
    p = _project(segments=[Segment(id="m", start=1.0, end=2.0, action="mute")])
    graph, _ = build_filter_graph(p, {})
    assert "volume=enable" in graph


def test_black_filter():
    p = _project(segments=[Segment(id="k", start=1.0, end=2.0, action="black")])
    graph, _ = build_filter_graph(p, {})
    assert "drawbox" in graph


def test_replace_trim_vs_loop(tmp_path):
    repl = tmp_path / "r.mp4"
    repl.write_bytes(b"x")
    p = _project(
        replacement_clips=["r.mp4"],
        segments=[
            Segment(
                id="r",
                start=1.0,
                end=3.0,
                action="replace",
                replacement_clip="r.mp4",
                replacement_mode="trim",
            )
        ],
    )
    info = ReplacementInfo(path=repl, has_audio=True, duration=10.0)
    graph_trim, extras = build_filter_graph(p, {"r.mp4": info})
    assert "trim=duration=2" in graph_trim
    assert "setpts=PTS-STARTPTS+1" in graph_trim
    assert "eof_action=pass" in graph_trim
    assert "adelay=1000:all=1" in graph_trim
    assert "amix=inputs=2:duration=first:normalize=0" in graph_trim
    assert len(extras) == 1

    p.segments[0].replacement_mode = "loop"
    graph_loop, _ = build_filter_graph(p, {"r.mp4": info})
    assert "loop=loop=-1" in graph_loop


def test_replace_short_clip_pads_before_trim():
    repl = Path("/tmp/r.mp4")
    p = _project(
        replacement_clips=["r.mp4"],
        segments=[
            Segment(
                id="r",
                start=0.0,
                end=5.0,
                action="replace",
                replacement_clip="r.mp4",
                replacement_mode="trim",
            )
        ],
    )
    info = ReplacementInfo(path=repl, has_audio=True, duration=2.0)
    graph, _ = build_filter_graph(p, {"r.mp4": info})
    assert "tpad=stop_mode=clone:stop_duration=3" in graph
    assert "apad," in graph


def test_replace_original_audio_passthrough():
    repl = Path("/tmp/r.mp4")
    p = _project(
        replacement_clips=["gen_r.mp4"],
        replacements=[
            ReplacementEntry(
                name="gen_r.mp4",
                duration=3.0,
                has_audio=False,
                width=320,
                height=180,
                audio_source="original",
            )
        ],
        segments=[
            Segment(
                id="r",
                start=1.0,
                end=2.0,
                action="replace",
                replacement_clip="gen_r.mp4",
            )
        ],
    )
    info = ReplacementInfo(
        path=repl, has_audio=False, duration=3.0, audio_source="original"
    )
    graph, _ = build_filter_graph(p, {"gen_r.mp4": info})
    assert "overlay=0:0" in graph
    assert "[1:a]" not in graph
    assert "amix" not in graph
    assert "volume=volume=0:enable='between(t\\,1" not in graph


def test_replace_no_replacement_audio_mutes_only():
    repl = Path("/tmp/r.mp4")
    p = _project(
        replacement_clips=["r.mp4"],
        segments=[
            Segment(
                id="r",
                start=1.0,
                end=2.0,
                action="replace",
                replacement_clip="r.mp4",
            )
        ],
    )
    info = ReplacementInfo(path=repl, has_audio=False, duration=3.0)
    graph, _ = build_filter_graph(p, {"r.mp4": info})
    assert "[1:a]" not in graph
    assert "volume=volume=0:enable=" in graph
    assert "amix" not in graph


def test_no_audio_source_uses_anullsrc():
    p = _project(has_audio=False, segments=[Segment(id="m", start=1.0, end=2.0, action="mute")])
    graph, _ = build_filter_graph(p, {})
    assert "anullsrc" in graph


def test_blur_then_cut():
    p = _project(
        segments=[
            Segment(id="b", start=1.0, end=2.0, action="blur"),
            Segment(id="c", start=3.0, end=4.0, action="cut"),
        ]
    )
    graph, _ = build_filter_graph(p, {})
    assert "gblur" in graph
    assert expected_output_duration(p) == 5.0


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg required")
def test_ffmpeg_export_blur_and_cut(tmp_path):
    src = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x180:rate=24:duration=6",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=6",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(src),
        ],
        check=True,
        capture_output=True,
    )
    data_dir = tmp_path / "data"
    pdir = data_dir / "projects" / "p1"
    pdir.mkdir(parents=True)
    shutil.copy(src, pdir / "source.mp4")
    project = _project(
        segments=[
            Segment(id="b", start=1.0, end=2.0, action="blur"),
            Segment(id="c", start=3.0, end=4.0, action="cut"),
        ]
    )
    out = pdir / "exports" / "out.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = build_export_command(project, out, data_dir)
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    meta = probe_video(out)
    assert 4.8 <= meta["duration"] <= 5.2
