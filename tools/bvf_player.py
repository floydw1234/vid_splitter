"""
Branched Video Format (BVF) Reference Player

Standalone Python reference player that reads .bvf files, resolves a viewer
profile, extracts segment blocks to temporary .ts files, and plays them via
ffplay with correct ordering.

Usage:
    python tools/bvf_player.py <file.bvf> [--profile adult] [--list] [--dry-run]
    python tools/bvf_player.py <file.bvf> --seek 30.0
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import struct
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

import zstandard

# ---------------------------------------------------------------------------
# BVF constants (mirrored from bvf_muxer.py — do not import to avoid circular)
# ---------------------------------------------------------------------------

FILE_MAGIC = b"BVF\x01\x00\x00\x00\x00"
BLOCK_MAGIC = b"SEG\x00"

FILE_HEADER_SIZE = 64
INDEX_ENTRY_SIZE = 40
BLOCK_HEADER_SIZE = 32
PACKET_HEADER_SIZE = 16

FLAG_MANIFEST_COMPRESSED = 0x00000001
FLAG_HAS_CHAPTERS = 0x00000002
FLAG_HAS_SUBTITLES = 0x00000004
FLAG_SEEKABLE = 0x00000008

CODEC_H264 = 0x00000001
CODEC_H265 = 0x00000002
CODEC_AV1 = 0x00000003
CODEC_VP9 = 0x00000004

CODEC_AAC_LC = 0x00000100
CODEC_OPUS = 0x00000101
CODEC_AC3 = 0x00000102
CODEC_EAC3 = 0x00000103

PACKET_VIDEO = 0x01
PACKET_AUDIO = 0x02
PACKET_SUBTITLE = 0x03

_CODEC_NAMES: dict[int, str] = {
    CODEC_H264: "H.264",
    CODEC_H265: "H.265",
    CODEC_AV1: "AV1",
    CODEC_VP9: "VP9",
    CODEC_AAC_LC: "AAC",
    CODEC_OPUS: "Opus",
    CODEC_AC3: "AC-3",
    CODEC_EAC3: "EAC-3",
}

# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_file_header(data: bytes) -> dict[str, Any]:
    """Parse a 64-byte file header into a dict."""
    (
        magic,
        version_major,
        version_minor,
        flags,
        index_offset,
        index_length,
        manifest_offset,
        manifest_length,
        segment_count,
        total_duration_ms,
        reserved,
    ) = struct.unpack("<8s H H I Q Q Q Q I Q I", data)

    return {
        "magic": magic.decode("ascii").rstrip("\x00"),
        "version_major": version_major,
        "version_minor": version_minor,
        "flags": flags,
        "index_offset": index_offset,
        "index_length": index_length,
        "manifest_offset": manifest_offset,
        "manifest_length": manifest_length,
        "segment_count": segment_count,
        "total_duration_ms": total_duration_ms,
        "reserved": reserved,
    }


def _parse_index_entry(data: bytes) -> dict[str, Any]:
    """Parse a 40-byte index entry into a dict."""
    segment_id_bytes, data_offset, data_length, duration_ms = struct.unpack(
        "<16s Q Q Q", data
    )
    return {
        "segment_id": segment_id_bytes.rstrip(b"\x00").decode("utf-8"),
        "data_offset": data_offset,
        "data_length": data_length,
        "duration_ms": duration_ms,
    }


# ---------------------------------------------------------------------------
# BVFPlayer
# ---------------------------------------------------------------------------


class BVFPlayer:
    """Reference player for BVF (Branched Video Format) files.

    Reads a .bvf file, resolves a viewer profile, extracts segment blocks
    to temporary files, and plays them via ffplay in the correct order.
    """

    SUPPORTED_PROFILES = ("adult", "teen", "teen_m", "teen_f", "child")

    def __init__(
        self,
        bvf_path: str | Path,
        profile: str | None = None,
        user_data: dict[str, Any] | None = None,
        verbose: bool = False,
    ) -> None:
        """Load and parse a BVF file.

        Args:
            bvf_path: Path to the .bvf file.
            profile: Optional viewer profile key from the BVF manifest.
            user_data: Optional user JSON data used to resolve a profile.
            verbose: Print detailed information to stdout.
        """
        self.bvf_path = Path(bvf_path).resolve()
        self.profile = profile
        self.user_data = user_data or {}
        self.verbose = verbose

        if not self.bvf_path.exists():
            raise FileNotFoundError(f"BVF file not found: {bvf_path}")

        # Parse the file
        with open(self.bvf_path, "rb") as f:
            self._raw_data = f.read()

        self.header = self._parse_header()
        self.segments = self._parse_index()
        self.manifest = self._parse_manifest()
        self.profile = self._resolve_profile(self.profile, self.user_data)

        self._playback_sequence: list[dict[str, Any]] | None = None
        self._temp_dir: tempfile.TemporaryDirectory[str] | None = None

        atexit.register(self.cleanup)

    # ------------------------------------------------------------------
    # File parsing
    # ------------------------------------------------------------------

    def _parse_header(self) -> dict[str, Any]:
        """Parse the 64-byte file header."""
        data = self._raw_data[:FILE_HEADER_SIZE]
        header = _parse_file_header(data)

        # Validate magic
        if header["magic"] != "BVF\x01":
            raise ValueError(
                f"Invalid BVF magic: {header['magic']!r} "
                f"(expected 'BVF\\x01')"
            )

        # Validate version
        if header["version_major"] != 1:
            raise ValueError(
                f"Unsupported BVF major version: {header['version_major']} "
                f"(expected 1)"
            )

        if self.verbose:
            print(
                f"[BVF] Header: v{header['version_major']}.{header['version_minor']}, "
                f"{header['segment_count']} segments, "
                f"{header['total_duration_ms'] / 1000:.1f}s total"
            )

        return header

    def _parse_index(self) -> list[dict[str, Any]]:
        """Parse the segment index (flat array of 40-byte entries)."""
        offset = self.header["index_offset"]
        entries: list[dict[str, Any]] = []

        for i in range(self.header["segment_count"]):
            entry_data = self._raw_data[offset + i * INDEX_ENTRY_SIZE : offset + (i + 1) * INDEX_ENTRY_SIZE]
            entry = _parse_index_entry(entry_data)
            entries.append(entry)

        if self.verbose:
            for seg in entries:
                print(
                    f"[BVF] Index: {seg['segment_id']} "
                    f"offset={seg['data_offset']} "
                    f"len={seg['data_length']} "
                    f"dur={seg['duration_ms']}ms"
                )

        return entries

    def _parse_manifest(self) -> dict[str, Any]:
        """Parse and decompress the JSON manifest."""
        offset = self.header["manifest_offset"]
        length = self.header["manifest_length"]
        compressed = self._raw_data[offset : offset + length]

        dctx = zstandard.ZstdDecompressor()
        manifest_json = dctx.decompress(compressed)
        manifest = json.loads(manifest_json.decode("utf-8"))

        if self.verbose:
            print(
                f"[BVF] Manifest: {manifest.get('title', 'unknown')}, "
                f"{len(manifest.get('segments', []))} segments"
            )

        return manifest

    # ------------------------------------------------------------------
    # Profile resolution
    # ------------------------------------------------------------------

    def _resolve_profile(self, explicit_profile: str | None, user_data: dict[str, Any]) -> str:
        profiles = self.manifest.get("profiles", {})

        if explicit_profile:
            return self._select_available_profile(explicit_profile, profiles)

        preferred = self._profile_from_user_data(user_data)
        return self._select_available_profile(preferred, profiles)

    def _profile_from_user_data(self, user_data: dict[str, Any]) -> str | None:
        user = user_data.get("user", user_data)
        override = user.get("profile_override") or user.get("profile")
        if override:
            return str(override)

        birthday = user.get("birthday") or user.get("date_of_birth")
        sex = str(user.get("sex") or user.get("gender") or "unset").lower()
        if birthday:
            try:
                born = date.fromisoformat(str(birthday))
            except ValueError:
                born = None
            if born is not None:
                today = date.today()
                age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
                if age < 13:
                    return "child"
                if age < 18:
                    return "teen_f" if sex == "female" else "teen_m"
                return "adult"

        return user_data.get("default_profile") or "adult"

    @staticmethod
    def _select_available_profile(preferred: str | None, profiles: dict[str, Any]) -> str:
        if preferred and preferred in profiles:
            return preferred
        if preferred in {"teen_m", "teen_f"} and "teen" in profiles:
            return "teen"
        for candidate in ("adult", "teen_m", "teen_f", "teen", "child"):
            if candidate in profiles:
                return candidate
        return next(iter(profiles), preferred or "adult")

    def resolve_playback_sequence(self) -> list[dict[str, Any]]:
        """Walk manifest segments and resolve the playback sequence.

        Returns an ordered list of playback entries (non-skip segments):
        [
            {
                "segment_id": "seg_001",     # narrative segment
                "action": "play",            # play | swap | mute
                "target_id": "seg_001",      # what to actually play
                "duration_ms": 300000,
                "start_ms": 0,
                "end_ms": 300000,
                "is_filler": False,
            },
            ...
        ]

        Filler segments (is_filler == True) are excluded from the sequence.
        Skip actions are also excluded.
        """
        if self._playback_sequence is not None:
            return self._playback_sequence

        segments = self.manifest.get("segments", [])
        index_map = {s["segment_id"]: s for s in self.segments}

        sequence: list[dict[str, Any]] = []

        for seg in segments:
            # Skip filler segments (not part of narrative timeline)
            if seg.get("is_filler", False):
                continue

            profile_entry = seg.get("profiles", {}).get(self.profile)
            if profile_entry is None:
                # No profile entry — default to play
                action = "play"
                target_id = seg["id"]
            else:
                action = profile_entry.get("action", "play")
                target_id = profile_entry.get("segment_id", seg["id"])

            if action == "skip":
                continue

            # Look up target segment in index
            target_seg = index_map.get(target_id)
            if target_seg is None:
                if self.verbose:
                    print(
                        f"[BVF] WARNING: target segment {target_id} not in index, skipping"
                    )
                continue

            entry = {
                "segment_id": seg["id"],
                "action": action,
                "target_id": target_id,
                "duration_ms": target_seg["duration_ms"],
                "start_ms": seg.get("start_ms", 0),
                "end_ms": seg.get("end_ms", 0),
                "is_filler": False,
            }
            sequence.append(entry)

        self._playback_sequence = sequence
        return sequence

    # ------------------------------------------------------------------
    # Segment extraction
    # ------------------------------------------------------------------

    def _get_temp_dir(self) -> str:
        """Get or create the temporary directory for extracted segments."""
        if self._temp_dir is None:
            self._temp_dir = tempfile.TemporaryDirectory(prefix="bvf_player_")
        return self._temp_dir.name

    def extract_segment(
        self,
        segment_id: str,
        output_path: str | Path,
    ) -> bool:
        """Extract a segment's raw data from the BVF file.

        Skips the 32-byte block header to get raw packet data.
        Writes concatenated packet data to output_path.

        Args:
            segment_id: Segment identifier (e.g. 'seg_001').
            output_path: Where to write the extracted data.

        Returns:
            True on success, False if segment not found.
        """
        # Find segment in index
        seg_entry = None
        for seg in self.segments:
            if seg["segment_id"] == segment_id:
                seg_entry = seg
                break

        if seg_entry is None:
            if self.verbose:
                print(f"[BVF] Segment {segment_id} not found in index")
            return False

        data_offset = seg_entry["data_offset"]
        data_length = seg_entry["data_length"]

        # Read the raw segment data (including block header)
        raw = self._raw_data[data_offset : data_offset + data_length]
        if len(raw) == 0:
            return False

        payload = self._extract_packet_payloads(raw)
        if not payload:
            return False

        output = Path(output_path)
        output.write_bytes(payload)
        return True

    def _extract_packet_payloads(self, raw: bytes, packet_type_filter: int | None = None) -> bytes:
        output_packets: list[bytes] = []
        pos = BLOCK_HEADER_SIZE
        while pos < len(raw):
            if pos + PACKET_HEADER_SIZE > len(raw):
                break
            packet_type = raw[pos]
            packet_size = struct.unpack_from("<I", raw, pos + 4)[0]
            packet_start = pos + PACKET_HEADER_SIZE
            packet_end = packet_start + packet_size
            if packet_end > len(raw):
                break
            if packet_type_filter is None or packet_type == packet_type_filter:
                output_packets.append(raw[packet_start:packet_end])
            pos = packet_end
        return b"".join(output_packets)

    def extract_video_only(
        self,
        segment_id: str,
        output_path: str | Path,
    ) -> bool:
        """Extract only video packets from a segment (for mute action).

        Drops audio packets, keeps video packets concatenated.

        Args:
            segment_id: Segment identifier.
            output_path: Where to write the extracted data.

        Returns:
            True on success, False if segment not found.
        """
        seg_entry = None
        for seg in self.segments:
            if seg["segment_id"] == segment_id:
                seg_entry = seg
                break

        if seg_entry is None:
            return False

        data_offset = seg_entry["data_offset"]
        data_length = seg_entry["data_length"]

        raw = self._raw_data[data_offset : data_offset + data_length]
        if len(raw) == 0:
            return False

        payload = self._extract_packet_payloads(raw, packet_type_filter=PACKET_VIDEO)
        output = Path(output_path)
        output.write_bytes(payload)
        return bool(payload)

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    def export(self, output_path: str | Path) -> Path:
        """Write the resolved playback sequence to one remuxed media file."""
        sequence = self.resolve_playback_sequence()
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="bvf_export_") as tmp:
            tmp_dir = Path(tmp)
            concat_lines: list[str] = []
            for i, entry in enumerate(sequence):
                segment_path = tmp_dir / f"seg_{i:03d}.ts"
                if entry["action"] == "mute":
                    success = self.extract_video_only(entry["target_id"], segment_path)
                else:
                    success = self.extract_segment(entry["target_id"], segment_path)
                if not success:
                    if self.verbose:
                        print(f"[BVF] WARNING: failed to extract {entry['target_id']}")
                    continue
                concat_lines.append(f"file '{segment_path}'")

            if not concat_lines:
                output.write_bytes(b"")
                return output

            concat_path = tmp_dir / "concat.txt"
            concat_path.write_text("\n".join(concat_lines), encoding="utf-8")
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-f", "concat",
                    "-safe", "0",
                    "-i", str(concat_path),
                    "-c", "copy",
                    str(output),
                ],
                check=True,
                capture_output=True,
            )
        return output

    def play(
        self,
        ffplay_args: list[str] | None = None,
    ) -> None:
        """Extract all segments in playback sequence order and play via ffplay.

        Uses ffplay's concat demuxer for sequential playback.
        Cleans up temp files on exit.

        Args:
            ffplay_args: Extra arguments for ffplay. Defaults to ['-autoexit', '-nodisp'].
        """
        sequence = self.resolve_playback_sequence()

        if not sequence:
            print("[BVF] No segments to play.")
            return

        temp_dir = self._get_temp_dir()

        # Build concat list and extract segments
        concat_lines: list[str] = []
        total_duration = 0.0

        for i, entry in enumerate(sequence):
            seg_id = entry["target_id"]
            action = entry["action"]
            duration_s = entry["duration_ms"] / 1000.0
            total_duration += duration_s

            out_path = os.path.join(temp_dir, f"seg_{i:03d}.ts")

            if action == "mute":
                success = self.extract_video_only(seg_id, out_path)
            else:
                success = self.extract_segment(seg_id, out_path)

            if not success:
                if self.verbose:
                    print(f"[BVF] WARNING: failed to extract {seg_id}")
                continue

            concat_lines.append(f"file '{out_path}'")
            concat_lines.append(f"duration {duration_s:.3f}")

            if self.verbose:
                print(
                    f"[BVF] {i + 1}. {entry['segment_id']} -> {seg_id} "
                    f"({action}, {entry['duration_ms']}ms)"
                )

        if not concat_lines:
            print("[BVF] No segments could be extracted.")
            return

        # Write concat file
        concat_path = os.path.join(temp_dir, "concat_list.txt")
        with open(concat_path, "w") as f:
            f.write("\n".join(concat_lines))

        # Build ffplay command
        cmd = [
            "ffplay",
            "-f", "concat",
            "-safe", "0",
            "-autoexit",
            "-nodisp",
            "-i", concat_path,
        ]

        if ffplay_args:
            cmd.extend(ffplay_args)

        if self.verbose:
            print(f"\n[BVF] Playing {len(sequence)} segments ({total_duration:.1f}s total)")
            print(f"[BVF] Command: {' '.join(cmd)}\n")

        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            print(f"[BVF] ffplay exited with code {e.returncode}")
            sys.exit(1)
        except FileNotFoundError:
            print("[BVF] ffplay not found. Install ffmpeg to enable playback.")
            sys.exit(1)

    def seek(self, position_ms: float) -> None:
        """Seek to a position in the profile-adjusted timeline.

        Computes which segment the position falls into based on accumulated
        playable durations (excluding skipped segments).

        Args:
            position_ms: Position in milliseconds in the profile-adjusted timeline.
        """
        sequence = self.resolve_playback_sequence()

        # Find the segment at this position
        accumulated = 0.0
        target_entry = None

        for entry in sequence:
            dur = entry["duration_ms"]
            if accumulated + dur > position_ms:
                target_entry = entry
                break
            accumulated += dur

        if target_entry is None:
            # Position is beyond the end — play last segment
            if sequence:
                target_entry = sequence[-1]
            else:
                print("[BVF] No segments to seek to.")
                return

        print(
            f"[BVF] Seek to {position_ms}ms -> "
            f"{target_entry['segment_id']} (target: {target_entry['target_id']})"
        )

        # Extract and play only the target segment
        temp_dir = self._get_temp_dir()
        out_path = os.path.join(temp_dir, "seek_target.ts")

        if target_entry["action"] == "mute":
            success = self.extract_video_only(target_entry["target_id"], out_path)
        else:
            success = self.extract_segment(target_entry["target_id"], out_path)

        if not success:
            print(f"[BVF] Failed to extract segment {target_entry['target_id']}")
            return

        cmd = [
            "ffplay",
            "-autoexit",
            "-nodisp",
            out_path,
        ]

        if self.verbose:
            print(f"[BVF] Seek command: {' '.join(cmd)}")

        try:
            subprocess.run(cmd, check=True)
        except FileNotFoundError:
            print("[BVF] ffplay not found. Install ffmpeg to enable playback.")

    def get_playback_info(self) -> dict[str, Any]:
        """Return metadata about the playback sequence."""
        sequence = self.resolve_playback_sequence()
        total_duration = sum(e["duration_ms"] for e in sequence)

        return {
            "title": self.manifest.get("title", "unknown"),
            "movie_id": self.manifest.get("movie_id", "unknown"),
            "profile": self.profile,
            "total_segments": len(sequence),
            "total_duration_ms": total_duration,
            "segments": [
                {
                    "narrative_id": e["segment_id"],
                    "action": e["action"],
                    "target_id": e["target_id"],
                    "duration_ms": e["duration_ms"],
                }
                for e in sequence
            ],
        }

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup(self) -> None:
        """Clean up temporary files."""
        if self._temp_dir is not None:
            try:
                self._temp_dir.cleanup()
            except Exception:
                pass
            self._temp_dir = None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="BVF Reference Player — play branched video files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s movie.bvf --profile adult\n"
            "  %(prog)s movie.bvf --list\n"
            "  %(prog)s movie.bvf --dry-run\n"
            "  %(prog)s movie.bvf --seek 30.0\n"
        ),
    )
    parser.add_argument(
        "bvf_file",
        help="Path to the .bvf file",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="Viewer profile override (default: resolve from --user-json, then adult)",
    )
    parser.add_argument(
        "--user-json",
        default=None,
        help="Path to user data JSON with birthday/sex/profile_override",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Show playback sequence, don't play",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would play without extracting or playing",
    )
    parser.add_argument(
        "--seek",
        type=float,
        default=None,
        help="Seek to position in seconds during playback",
    )
    parser.add_argument(
        "--export",
        default=None,
        help="Write the resolved stream to a remuxed media file instead of launching ffplay",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print detailed information",
    )
    parser.add_argument(
        "--ffplay-args",
        default=None,
        help="Extra arguments passed to ffplay (default: -autoexit -nodisp)",
    )
    return parser


def main() -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    user_data = None
    if args.user_json:
        with open(args.user_json, "r", encoding="utf-8") as f:
            user_data = json.load(f)

    try:
        player = BVFPlayer(
            bvf_path=args.bvf_file,
            profile=args.profile,
            user_data=user_data,
            verbose=args.verbose,
        )
    except (FileNotFoundError, ValueError) as e:
        print(f"[BVF] Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.list:
        # Show playback sequence
        sequence = player.resolve_playback_sequence()
        info = player.get_playback_info()

        print(f"\nTitle: {info['title']}")
        print(f"Profile: {player.profile}")
        print(f"Total segments: {info['total_segments']}")
        print(f"Total duration: {info['total_duration_ms'] / 1000:.1f}s")
        print("-" * 70)

        for i, entry in enumerate(sequence, 1):
            print(
                f"  {i:3d}. {entry['segment_id']:20s} -> {entry['target_id']:20s} "
                f"[{entry['action']:6s}] {entry['duration_ms'] / 1000:7.1f}s"
            )

        print("-" * 70)
        return

    if args.export:
        out = player.export(args.export)
        info = player.get_playback_info()
        print(f"[BVF] Exported {info['total_segments']} segments ({info['total_duration_ms'] / 1000:.1f}s) for profile {player.profile}: {out}")
        return

    if args.dry_run:
        # Show what would play without extracting
        sequence = player.resolve_playback_sequence()
        info = player.get_playback_info()

        print(f"\nTitle: {info['title']}")
        print(f"Profile: {player.profile}")
        print(f"Total segments: {info['total_segments']}")
        print(f"Total duration: {info['total_duration_ms'] / 1000:.1f}s")
        print("-" * 70)

        for i, entry in enumerate(sequence, 1):
            print(
                f"  {i:3d}. {entry['segment_id']:20s} -> {entry['target_id']:20s} "
                f"[{entry['action']:6s}] {entry['duration_ms'] / 1000:7.1f}s"
            )

        print("-" * 70)
        print("\n[Dry run] No files extracted or played.")
        return

    # Normal playback mode
    if args.seek is not None:
        player.seek(args.seek * 1000)  # Convert seconds to ms
    else:
        ffplay_args = None
        if args.ffplay_args:
            ffplay_args = args.ffplay_args.split()
        player.play(ffplay_args=ffplay_args)


if __name__ == "__main__":
    main()
