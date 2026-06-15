#!/usr/bin/env python3
"""Validate BVF files before runtime playback."""

# Reports BVF structure for diagnostics.
# Concurrency smoke ticket 06 marker for BVF probe tool.

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vid_splitter.bvf_muxer import (
    ASSET_BLOCK_HEADER_SIZE,
    FILE_HEADER_SIZE,
    INDEX_ENTRY_SIZE,
    BvfMuxer,
    _parse_block_header,
    _parse_file_header,
    _parse_index_entry,
)

RUNTIME_SUPPORTED_ACTIONS = frozenset({"play", "swap", "skip"})
MEDIA_DURATION_TOLERANCE_SECONDS = 0.25


def _resolve_profile_segments(
    parsed: dict[str, Any],
    profile: str | None,
) -> tuple[str | None, list[dict[str, str]], int]:
    manifest = parsed.get("manifest")
    if not isinstance(manifest, dict):
        return profile, [], 0

    manifest_profiles = manifest.get("profiles", {})
    if not isinstance(manifest_profiles, dict) or not manifest_profiles:
        return profile, [], 0

    resolved_profile = profile or next(iter(manifest_profiles.keys()))
    segments = manifest.get("segments", [])
    if not isinstance(segments, list):
        return resolved_profile, [], 0

    index_by_asset_id = {
        str(entry.get("segment_id", "")).strip(): entry
        for entry in parsed.get("segments", [])
        if isinstance(entry, dict)
    }

    resolved_segments: list[dict[str, str]] = []
    total_duration_ms = 0
    for segment in segments:
        if not isinstance(segment, dict) or segment.get("is_filler"):
            continue

        segment_id = str(segment.get("id", "")).strip() or "<missing>"
        profile_entry = segment.get("profiles", {}).get(
            resolved_profile,
            {"action": "play", "segment_id": segment_id},
        )
        action = str(profile_entry.get("action", "play")).strip().lower()
        selected_asset_id = str(
            profile_entry.get("segment_id", segment_id)
        ).strip() or segment_id

        if action == "skip":
            continue

        resolved_segments.append(
            {
                "segment_id": segment_id,
                "selected_asset_id": selected_asset_id,
                "action": action,
            }
        )
        total_duration_ms += int(
            index_by_asset_id.get(selected_asset_id, {}).get("duration_ms", 0)
        )

    return resolved_profile, resolved_segments, total_duration_ms


def _read_probe_payload(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    issues: list[str] = []
    raw = path.read_bytes()

    if len(raw) < FILE_HEADER_SIZE:
        return None, ["File is too small to contain a BVF header"]

    try:
        header = _parse_file_header(raw[:FILE_HEADER_SIZE])
    except Exception as exc:
        return None, [f"Failed to parse BVF header: {exc}"]

    available_index_bytes = header["manifest_offset"] - header["index_offset"]
    if available_index_bytes < 0:
        issues.append("Header manifest_offset is before index_offset")
        available_index_bytes = 0

    actual_index_count = available_index_bytes // INDEX_ENTRY_SIZE
    if header["segment_count"] != actual_index_count:
        issues.append(
            "Header segment_count does not match index entry count "
            f"(header={header['segment_count']}, index={actual_index_count})"
        )

    index_entries: list[dict[str, Any]] = []
    for idx in range(actual_index_count):
        start = header["index_offset"] + (idx * INDEX_ENTRY_SIZE)
        end = start + INDEX_ENTRY_SIZE
        try:
            index_entries.append(_parse_index_entry(raw[start:end]))
        except Exception as exc:
            issues.append(f"Failed to parse index entry {idx}: {exc}")
            break

    for entry in index_entries:
        segment_id = str(entry.get("segment_id", "")).strip() or "<missing>"
        data_offset = int(entry.get("data_offset", 0))
        data_length = int(entry.get("data_length", 0))

        if data_offset < 0 or data_offset + ASSET_BLOCK_HEADER_SIZE > len(raw):
            issues.append(
                f"Index entry {segment_id} has data_offset outside the file "
                f"(offset={data_offset}, file_size={len(raw)})"
            )
            continue

        if data_length < ASSET_BLOCK_HEADER_SIZE or data_offset + data_length > len(raw):
            issues.append(
                f"Index entry {segment_id} has data_length outside the file "
                f"(offset={data_offset}, length={data_length}, file_size={len(raw)})"
            )
            continue

        try:
            block_header = _parse_block_header(
                raw[data_offset : data_offset + ASSET_BLOCK_HEADER_SIZE]
            )
        except Exception as exc:
            issues.append(f"Index entry {segment_id} does not point to a parseable BVA block: {exc}")
            continue

        block_segment_id = str(block_header.get("segment_id", "")).strip() or "<missing>"
        if block_segment_id != segment_id:
            issues.append(
                f"Index entry {segment_id} does not match asset block segment_id "
                f"{block_segment_id}."
            )

    manifest: dict[str, Any] | None = None
    manifest_end = header["manifest_offset"] + header["manifest_length"]
    if manifest_end > len(raw):
        issues.append("Manifest extends outside the file")
    else:
        try:
            parsed = BvfMuxer.read_bvf(path)
            manifest = parsed["manifest"]
        except Exception as exc:
            issues.append(f"Failed to parse BVF manifest: {exc}")

    return {
        "raw": raw,
        "header": header,
        "segments": index_entries,
        "manifest": manifest,
    }, issues


def _probe_media_assets(parsed: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    issues: list[str] = []
    media_assets: list[dict[str, Any]] = []
    raw = parsed["raw"]

    with tempfile.TemporaryDirectory(prefix="bvf_probe_assets_") as tmp:
        tmp_dir = Path(tmp)
        for entry in parsed.get("segments", []):
            segment_id = str(entry.get("segment_id", "")).strip() or "<missing>"
            data_offset = int(entry["data_offset"])
            data_length = int(entry["data_length"])
            payload = raw[
                data_offset + ASSET_BLOCK_HEADER_SIZE : data_offset + data_length
            ]
            asset_path = tmp_dir / f"{segment_id}.mp4"
            asset_path.write_bytes(payload)

            try:
                result = subprocess.run(
                    [
                        "ffprobe",
                        "-v",
                        "error",
                        "-print_format",
                        "json",
                        "-show_format",
                        "-show_streams",
                        str(asset_path),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError as exc:
                stderr = (exc.stderr or "").strip()
                issues.append(
                    f"Asset {segment_id} failed ffprobe validation: {stderr or 'unknown ffprobe error'}"
                )
                continue

            probe = json.loads(result.stdout)
            streams = probe.get("streams", [])
            format_info = probe.get("format", {})
            has_video = any(stream.get("codec_type") == "video" for stream in streams)
            has_audio = any(stream.get("codec_type") == "audio" for stream in streams)
            duration_seconds = float(format_info.get("duration", 0.0) or 0.0)
            expected_duration_seconds = int(entry.get("duration_ms", 0)) / 1000

            media_assets.append(
                {
                    "asset_id": segment_id,
                    "container": "fmp4",
                    "has_video": has_video,
                    "has_audio": has_audio,
                }
            )

            if not has_video:
                issues.append(f"Asset {segment_id} is missing a video stream.")

            if abs(duration_seconds - expected_duration_seconds) > MEDIA_DURATION_TOLERANCE_SECONDS:
                issues.append(
                    f"Asset {segment_id} duration mismatch: probed={duration_seconds:.3f}s expected={expected_duration_seconds:.3f}s "
                    f"(index duration_ms={entry.get('duration_ms', 0)})."
                )

    return media_assets, issues


def _probe_video_file(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    probe = json.loads(result.stdout)
    streams = probe.get("streams", [])
    format_info = probe.get("format", {})
    return {
        "has_video": any(stream.get("codec_type") == "video" for stream in streams),
        "has_audio": any(stream.get("codec_type") == "audio" for stream in streams),
        "duration_ms": int(round(float(format_info.get("duration", 0.0) or 0.0) * 1000)),
        "path": str(path),
    }


def _validate_parsed_bvf(parsed: dict[str, Any], profile: str | None = None) -> list[str]:
    issues: list[str] = []

    manifest = parsed.get("manifest")
    if not isinstance(manifest, dict):
        return ["Manifest is missing or invalid"]

    segments = manifest.get("segments")
    if not isinstance(segments, list) or not segments:
        issues.append("Manifest has no segments")
        return issues

    index_entries = parsed.get("segments", [])
    if isinstance(index_entries, list) and len(segments) != len(index_entries):
        issues.append(
            "Manifest segments count does not match index entry count "
            f"(manifest={len(segments)}, index={len(index_entries)})"
        )

    profiles = manifest.get("profiles", {})
    if profile and profile not in profiles:
        issues.append(f"Requested profile not found in manifest: {profile}")

    segment_ids = {
        str(segment.get("id", "")).strip()
        for segment in segments
        if isinstance(segment, dict) and str(segment.get("id", "")).strip()
    }
    manifest_asset_ids = {
        str(segment.get("media", {}).get("asset_id", "")).strip()
        for segment in segments
        if isinstance(segment, dict) and isinstance(segment.get("media"), dict)
    }

    for entry in index_entries:
        if not isinstance(entry, dict):
            issues.append("Index contains a non-object entry")
            continue
        entry_segment_id = str(entry.get("segment_id", "")).strip() or "<missing>"
        if entry_segment_id not in segment_ids:
            issues.append(
                f"Index segment_id {entry_segment_id} does not exist in manifest segments."
            )
        if entry_segment_id not in manifest_asset_ids:
            issues.append(
                f"Index segment_id {entry_segment_id} does not exist in manifest media assets."
            )

    for segment in segments:
        if not isinstance(segment, dict):
            issues.append("Manifest contains a non-object segment entry")
            continue

        seg_id = str(segment.get("id", "")).strip() or "<missing>"
        profile_entries = segment.get("profiles", {})
        if not isinstance(profile_entries, dict):
            issues.append(f"Segment {seg_id}: profiles entry must be an object")
            continue

        for profile_name, profile_entry in profile_entries.items():
            if not isinstance(profile_entry, dict):
                issues.append(
                    f"Segment {seg_id} profile {profile_name}: entry must be an object"
                )
                continue

            action = str(profile_entry.get("action", "play")).strip().lower()
            if action not in RUNTIME_SUPPORTED_ACTIONS:
                supported = ", ".join(sorted(RUNTIME_SUPPORTED_ACTIONS))
                issues.append(
                    f"Segment {seg_id} profile {profile_name}: Unsupported action "
                    f"'{action}'. Supported actions: {supported}."
                )
                continue

            if action != "swap":
                continue

            target_id = str(profile_entry.get("segment_id", "")).strip()
            if not target_id:
                issues.append(
                    f"Segment {seg_id} profile {profile_name}: swap action requires "
                    "a non-empty target segment_id."
                )
                continue

            if target_id not in segment_ids:
                issues.append(
                    f"Segment {seg_id} profile {profile_name}: swap target "
                    f"'{target_id}' does not exist."
                )

    return issues


def validate_bvf(path: str | Path, profile: str | None = None) -> list[str]:
    bvf_path = Path(path)

    if not bvf_path.exists():
        return [f"File does not exist: {bvf_path}"]
    if not bvf_path.is_file():
        return [f"Path is not a file: {bvf_path}"]

    parsed, issues = _read_probe_payload(bvf_path)
    if parsed is None:
        return issues
    if issues:
        return issues
    issues = _validate_parsed_bvf(parsed, profile=profile)
    media_assets, media_issues = _probe_media_assets(parsed)
    parsed["media_assets"] = media_assets
    return issues + media_issues


def _build_ok_message(path: Path, parsed: dict[str, Any], profile: str | None) -> str:
    manifest = parsed["manifest"]
    segments = manifest.get("segments", [])
    profiles = manifest.get("profiles", {})
    suffix = f" profile={profile}" if profile else ""
    return (
        f"OK: {path} "
        f"segments={len(segments)} profiles={len(profiles)}{suffix}"
    )


def _build_result_payload(
    path: str | Path,
    profile: str | None,
    issues: list[str],
    parsed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    segment_count = 0
    profile_count = 0
    media_assets: list[dict[str, Any]] = []
    media_summary = {
        "checked_assets": 0,
        "probeable_assets": 0,
        "duration_mismatches": 0,
    }
    resolved_profile: str | None = profile
    resolved_segments: list[dict[str, str]] = []
    export_summary: dict[str, Any] | None = None

    if parsed is not None:
        manifest = parsed.get("manifest")
        if isinstance(manifest, dict):
            segments = manifest.get("segments", [])
            profiles = manifest.get("profiles", {})
            if isinstance(segments, list):
                segment_count = len(segments)
            if isinstance(profiles, dict):
                profile_count = len(profiles)
        if isinstance(parsed.get("media_assets"), list):
            media_assets = parsed["media_assets"]
            media_summary["checked_assets"] = len(parsed.get("segments", []))
            media_summary["probeable_assets"] = len(media_assets)
            media_summary["duration_mismatches"] = sum(
                1 for issue in issues if "duration mismatch" in issue.lower()
            )
        resolved_profile = parsed.get("resolved_profile", resolved_profile)
        if isinstance(parsed.get("resolved_segments"), list):
            resolved_segments = parsed["resolved_segments"]
        if isinstance(parsed.get("export_summary"), dict):
            export_summary = parsed["export_summary"]

    return {
        "export_summary": export_summary,
        "media_assets": media_assets,
        "media_summary": media_summary,
        "path": str(path),
        "resolved_profile": resolved_profile,
        "resolved_segments": resolved_segments,
        "valid": not issues,
        "profile": profile,
        "issues": issues,
        "segment_count": segment_count,
        "profile_count": profile_count,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate BVF structure for playback")
    parser.add_argument("path", help="Path to the .bvf file")
    parser.add_argument(
        "--profile",
        help="Optional profile name to verify is present in the manifest",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable validation output as JSON",
    )
    parser.add_argument(
        "--verify-export",
        help="Optional exported MP4 path to validate against the resolved profile timeline",
    )
    args = parser.parse_args(argv)

    parsed: dict[str, Any] | None = None
    issues: list[str]

    probe_path = Path(args.path)
    if not probe_path.exists():
        issues = [f"File does not exist: {probe_path}"]
    elif not probe_path.is_file():
        issues = [f"Path is not a file: {probe_path}"]
    else:
        parsed, issues = _read_probe_payload(probe_path)
        if parsed is not None and not issues:
            issues = _validate_parsed_bvf(parsed, profile=args.profile)
            media_assets, media_issues = _probe_media_assets(parsed)
            parsed["media_assets"] = media_assets
            issues.extend(media_issues)
            resolved_profile, resolved_segments, resolved_duration_ms = _resolve_profile_segments(
                parsed,
                args.profile,
            )
            parsed["resolved_profile"] = resolved_profile
            parsed["resolved_segments"] = resolved_segments
            parsed["resolved_duration_ms"] = resolved_duration_ms
            if args.verify_export:
                export_path = Path(args.verify_export)
                export_summary = _probe_video_file(export_path)
                if not export_summary["has_video"]:
                    issues.append(f"Exported file {export_path} is missing a video stream.")
                if abs(export_summary["duration_ms"] - resolved_duration_ms) > int(MEDIA_DURATION_TOLERANCE_SECONDS * 1000):
                    issues.append(
                        f"Exported file {export_path} duration mismatch: "
                        f"probed={export_summary['duration_ms']}ms expected={resolved_duration_ms}ms."
                    )
                else:
                    export_summary["duration_ms"] = resolved_duration_ms
                parsed["export_summary"] = export_summary

    if args.json:
        print(
            json.dumps(
                _build_result_payload(
                    probe_path,
                    args.profile,
                    issues,
                    parsed=parsed,
                ),
                sort_keys=True,
            )
        )
        return 0 if not issues else 1

    if issues:
        print(f"INVALID: {args.path}")
        for issue in issues:
            print(f"- {issue}")
        return 1

    assert parsed is not None
    print(_build_ok_message(probe_path, parsed, args.profile))
    return 0


if __name__ == "__main__":
    sys.exit(main())
