"""ffmpeg export: pure filter graph and command builders."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from editor.ffmpeg_util import probe_video
from editor.project import Project, ReplacementEntry, Segment


@dataclass(frozen=True)
class ReplacementInfo:
    path: Path
    has_audio: bool
    duration: float


def segments_to_apply(project: Project) -> list[Segment]:
    return [s for s in project.segments if s.action != "none"]


def merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not intervals:
        return []
    sorted_iv = sorted(intervals, key=lambda x: x[0])
    merged: list[tuple[float, float]] = [sorted_iv[0]]
    for start, end in sorted_iv[1:]:
        last_s, last_e = merged[-1]
        if start <= last_e:
            merged[-1] = (last_s, max(last_e, end))
        else:
            merged.append((start, end))
    return merged


def complement_intervals(
    duration: float, cuts: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    if not cuts:
        return [(0.0, duration)]
    merged = merge_intervals(cuts)
    keep: list[tuple[float, float]] = []
    pos = 0.0
    for start, end in merged:
        if start > pos:
            keep.append((pos, start))
        pos = max(pos, end)
    if pos < duration:
        keep.append((pos, duration))
    return [(s, e) for s, e in keep if e - s > 1e-6]


def build_filter_graph(
    project: Project,
    replacement_paths: dict[str, ReplacementInfo],
) -> tuple[str, list[str]]:
    """
    Effects on the original timeline, then cut/concat.

    Returns (filter_graph, extra input files after the source).
    """
    applied = segments_to_apply(project)
    if not applied:
        return "", []
    cuts = merge_intervals([(s.start, s.end) for s in applied if s.action == "cut"])
    effects = sorted(
        [s for s in applied if s.action != "cut"],
        key=lambda s: (s.start, s.end, s.id),
    )

    w, h = project.width, project.height
    fps = project.fps
    duration = project.duration

    extra_inputs: list[str] = []
    input_map: dict[str, int] = {}
    for seg in effects:
        if seg.action == "replace" and seg.replacement_clip:
            name = seg.replacement_clip
            if name not in input_map:
                info = replacement_paths.get(name)
                if info is None:
                    raise ValueError(f"missing replacement clip: {name}")
                input_map[name] = len(extra_inputs) + 1
                extra_inputs.append(str(info.path.resolve()))

    parts: list[str] = []
    parts.append("[0:v]format=yuv420p[v0]")
    if project.has_audio:
        parts.append("[0:a]anull[a0]")
    else:
        parts.append(
            f"anullsrc=channel_layout=stereo:sample_rate=48000,atrim=0:{duration:.6f},asetpts=PTS-STARTPTS[a0]"
        )

    v_out = "[v0]"
    a_out = "[a0]"
    idx = 0

    for seg in effects:
        s, e = seg.start, seg.end
        en = f"between(t\\,{s:.6f}\\,{e:.6f})"
        if seg.action == "blur":
            idx += 1
            parts.append(
                f"{v_out}gblur=sigma=40:steps=3:enable='{en}'[v{idx}]"
            )
            v_out = f"[v{idx}]"
        elif seg.action == "black":
            idx += 1
            parts.append(
                f"{v_out}drawbox=x=0:y=0:w=iw:h=ih:color=black@1:t=fill:enable='{en}'[v{idx}]"
            )
            v_out = f"[v{idx}]"
        elif seg.action == "mute":
            idx += 1
            parts.append(f"{a_out}volume=enable='{en}':volume=0[a{idx}]")
            a_out = f"[a{idx}]"
        elif seg.action == "replace" and seg.replacement_clip:
            inp = input_map[seg.replacement_clip]
            info = replacement_paths[seg.replacement_clip]
            seg_dur = max(0.01, e - s)
            idx += 1
            mode = seg.replacement_mode or "trim"
            pts_shift = f"setpts=PTS-STARTPTS+{s:.6f}/TB"
            ms = int(round(s * 1000.0))
            if mode == "loop":
                trim_v = (
                    f"loop=loop=-1:size=32767:start=0,trim=duration={seg_dur:.6f},{pts_shift}"
                )
                trim_a = (
                    f"aloop=loop=-1:size=32767,atrim=duration={seg_dur:.6f},asetpts=PTS-STARTPTS"
                )
            else:
                pad_v = ""
                pad_a = ""
                if info.duration < seg_dur:
                    pad_len = seg_dur - info.duration
                    pad_v = f"tpad=stop_mode=clone:stop_duration={pad_len:.6f},"
                    pad_a = "apad,"
                trim_v = f"{pad_v}trim=duration={seg_dur:.6f},{pts_shift}"
                trim_a = f"{pad_a}atrim=duration={seg_dur:.6f},asetpts=PTS-STARTPTS"
            v_chain = (
                f"[{inp}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps={fps:.6f},{trim_v}[rv{idx}];"
                f"{v_out}[rv{idx}]overlay=0:0:eof_action=pass:enable='{en}'[v{idx}]"
            )
            parts.append(v_chain)
            v_out = f"[v{idx}]"
            if info.has_audio:
                parts.append(
                    f"[{inp}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                    f"{trim_a},adelay={ms}:all=1[ra{idx}];"
                    f"{a_out}volume=volume=0:enable='{en}'[am{idx}];"
                    f"[am{idx}][ra{idx}]amix=inputs=2:duration=first:normalize=0[a{idx}]"
                )
            else:
                parts.append(f"{a_out}volume=volume=0:enable='{en}'[a{idx}]")
            a_out = f"[a{idx}]"

    veff, aeff = v_out, a_out
    keep = complement_intervals(duration, cuts)
    if not cuts:
        parts.append(f"{veff}null[vout]")
        parts.append(f"{aeff}anull[aout]")
    elif len(keep) == 1:
        ks, ke = keep[0]
        parts.append(
            f"{veff}trim=start={ks:.6f}:end={ke:.6f},setpts=PTS-STARTPTS[vout];"
            f"{aeff}atrim=start={ks:.6f}:end={ke:.6f},asetpts=PTS-STARTPTS[aout]"
        )
    else:
        n = len(keep)
        parts.append(f"{veff}split={n}" + "".join(f"[vsplit{i}]" for i in range(n)))
        parts.append(f"{aeff}asplit={n}" + "".join(f"[asplit{i}]" for i in range(n)))
        for i, (ks, ke) in enumerate(keep):
            parts.append(
                f"[vsplit{i}]trim=start={ks:.6f}:end={ke:.6f},setpts=PTS-STARTPTS[vk{i}];"
                f"[asplit{i}]atrim=start={ks:.6f}:end={ke:.6f},asetpts=PTS-STARTPTS[ak{i}]"
            )
        vin = "".join(f"[vk{i}]" for i in range(n))
        ain = "".join(f"[ak{i}]" for i in range(n))
        parts.append(
            f"{vin}concat=n={n}:v=1:a=0[vout];"
            f"{ain}concat=n={n}:v=0:a=1[aout]"
        )

    return ";".join(parts), extra_inputs


def expected_output_duration(project: Project) -> float:
    applied = segments_to_apply(project)
    cuts = merge_intervals([(s.start, s.end) for s in applied if s.action == "cut"])
    keep = complement_intervals(project.duration, cuts)
    return sum(e - s for s, e in keep)


def project_paths(project: Project, data_dir: Path) -> tuple[Path, Path]:
    pdir = data_dir / "projects" / project.id
    source = pdir / project.source_video
    repl_dir = pdir / "replacements"
    return source, repl_dir


def _replacement_info_map(
    project: Project, repl_dir: Path
) -> dict[str, ReplacementInfo]:
    meta_by_name: dict[str, ReplacementEntry] = {r.name: r for r in project.replacements}
    out: dict[str, ReplacementInfo] = {}
    for name in project.replacement_clips:
        path = repl_dir / name
        entry = meta_by_name.get(name)
        if entry:
            duration = float(entry.duration)
            has_audio = entry.has_audio
        else:
            duration = 0.0
            has_audio = True
        if duration <= 0.0:
            probed = probe_video(path)
            duration = float(probed["duration"])
            if entry is None:
                has_audio = bool(probed["has_audio"])
        out[name] = ReplacementInfo(path=path, has_audio=has_audio, duration=duration)
    return out


def build_export_command(
    project: Project, output_path: Path, data_dir: Path
) -> list[str]:
    source, repl_dir = project_paths(project, data_dir)
    replacement_paths = _replacement_info_map(project, repl_dir)
    graph, extras = build_filter_graph(project, replacement_paths)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source)]
    for extra in extras:
        cmd.extend(["-i", extra])
    if graph:
        cmd.extend(["-filter_complex", graph, "-map", "[vout]", "-map", "[aout]"])
    else:
        cmd.extend(["-map", "0:v", "-map", "0:a?"])
    cmd.extend(
        [
            "-c:v",
            "libx264",
            "-crf",
            "20",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    return cmd


def run_export_with_progress(
    project: Project,
    output_path: Path,
    data_dir: Path,
    on_progress: Callable[[float], None],
) -> None:
    cmd = build_export_command(project, output_path, data_dir)
    expected_ms = max(1.0, expected_output_duration(project) * 1000.0)
    insert_at = cmd.index("-c:v")
    cmd = cmd[:insert_at] + ["-progress", "pipe:1", "-nostats"] + cmd[insert_at:]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.stdout:
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("out_time_ms="):
                try:
                    out_time_ms = float(line.split("=", 1)[1])
                    on_progress(min(0.99, out_time_ms / expected_ms))
                except ValueError:
                    pass
            elif line == "progress=end":
                on_progress(1.0)
    stderr = proc.stderr.read() if proc.stderr else ""
    code = proc.wait()
    if code != 0:
        raise RuntimeError(stderr or f"ffmpeg failed with code {code}")
