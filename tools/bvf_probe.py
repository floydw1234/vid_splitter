#!/usr/bin/env python3
"""Validate BVF files before runtime playback."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vid_splitter.bvf_muxer import BvfMuxer

RUNTIME_SUPPORTED_ACTIONS = frozenset({"play", "swap", "skip"})


def _validate_parsed_bvf(parsed: dict[str, Any], profile: str | None = None) -> list[str]:
    issues: list[str] = []

    manifest = parsed.get("manifest")
    if not isinstance(manifest, dict):
        return ["Manifest is missing or invalid"]

    segments = manifest.get("segments")
    if not isinstance(segments, list) or not segments:
        issues.append("Manifest has no segments")
        return issues

    profiles = manifest.get("profiles", {})
    if profile and profile not in profiles:
        issues.append(f"Requested profile not found in manifest: {profile}")

    segment_ids = {
        str(segment.get("id", "")).strip()
        for segment in segments
        if isinstance(segment, dict) and str(segment.get("id", "")).strip()
    }

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

    try:
        parsed = BvfMuxer.read_bvf(bvf_path)
    except Exception as exc:
        return [f"Failed to parse BVF: {exc}"]

    return _validate_parsed_bvf(parsed, profile=profile)


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

    if parsed is not None:
        manifest = parsed.get("manifest")
        if isinstance(manifest, dict):
            segments = manifest.get("segments", [])
            profiles = manifest.get("profiles", {})
            if isinstance(segments, list):
                segment_count = len(segments)
            if isinstance(profiles, dict):
                profile_count = len(profiles)

    return {
        "path": str(path),
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
    args = parser.parse_args(argv)

    parsed: dict[str, Any] | None = None
    issues: list[str]

    probe_path = Path(args.path)
    if not probe_path.exists():
        issues = [f"File does not exist: {probe_path}"]
    elif not probe_path.is_file():
        issues = [f"Path is not a file: {probe_path}"]
    else:
        try:
            parsed = BvfMuxer.read_bvf(probe_path)
        except Exception as exc:
            issues = [f"Failed to parse BVF: {exc}"]
        else:
            issues = _validate_parsed_bvf(parsed, profile=args.profile)

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
