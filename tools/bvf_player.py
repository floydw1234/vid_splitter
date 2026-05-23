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
        "magic": magic.decode("ascii"),
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

    SUPPORTED_PROFILES = ("adult", "teen", "child")

    def __init__(
        self,
        bvf_path: str | Path,
        profile: str = "adult",
        verbose: bool = False,
    ) -> None:
        """Load and parse a BVF file.

        Args:
            bvf_path: Path to the .bvf file.
            profile: Viewer profile — one of 'adult', 'teen', 'child'.
            verbose: Print detailed information to stdout.
        """
        self.bvf_path = Path(bvf_path).resolve()
        self.profile = profile
        self.verbose = verbose

        if not self.bvf_path.exists():
            raise FileNotFoundError(f"BVF file not found: {bvf_path}")

        # Parse the file
        with open(self.bvf_path, "rb") as f:
            self._raw_data = f.read()

        self.header = self._parse_header()
        self.segments = self._parse_index()
        self.manifest = self._parse_manifest()

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

        profile_filter = set(self.manifest.get("profiles", {}).get(self.profile, {}).get("filters", []))
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

        # Skip the 32-byte block header to get raw packet data
        output = Path(output_path)
        output.write_bytes(raw[BLOCK_HEADER_SIZE:])
        return True

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

        # Parse packets and keep only video packets
        output_packets: list[bytes] = []
        pos = BLOCK_HEADER_SIZE  # skip block header

        while pos < len(raw):
            if pos + PACKET_HEADER_SIZE > len(raw):
                break

            packet_type = raw[pos]
            # reserved = raw[pos + 1:pos + 4]  # 3 bytes, ignored
            packet_size = struct.unpack_from("<I", raw, pos + 4)[0]
            # pts_ms = struct.unpack_from("<Q", raw, pos + 8)[0]

            if pos + PACKET_HEADER_SIZE + packet_size > len(raw):
                break

            packet_data = raw[pos + PACKET_HEADER_SIZE : pos + PACKET_HEADER_SIZE + packet_size]

            if packet_type == PACKET_VIDEO:
                output_packets.append(packet_data)

            pos += PACKET_HEADER_SIZE + packet_size

        output = Path(output_path)
        output.write_bytes(b"".join(output_packets))
        return len(output_packets) > 0

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

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
        choices=BVFPlayer.SUPPORTED_PROFILES,
        default="adult",
        help="Viewer profile (default: adult)",
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

    try:
        player = BVFPlayer(
            bvf_path=args.bvf_file,
            profile=args.profile,
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
        print(f"Profile: {args.profile}")
        print(f"Total segments: {info['total_segments']}")
        print(f"Total duration: {info['total_duration_ms'] / 1000:.1f}s")
        print("-" * 70)

        for i, entry in enumerate(sequence, 1):
            print(
                f"  {i:3d}. {entry['narrative_id']:20s} -> {entry['target_id']:20s} "
                f"[{entry['action']:6s}] {entry['duration_ms'] / 1000:7.1f}s"
            )

        print("-" * 70)
        return

    if args.dry_run:
        # Show what would play without extracting
        sequence = player.resolve_playback_sequence()
        info = player.get_playback_info()

        print(f"\nTitle: {info['title']}")
        print(f"Profile: {args.profile}")
        print(f"Total segments: {info['total_segments']}")
        print(f"Total duration: {info['total_duration_ms'] / 1000:.1f}s")
        print("-" * 70)

        for i, entry in enumerate(sequence, 1):
            print(
                f"  {i:3d}. {entry['narrative_id']:20s} -> {entry['target_id']:20s} "
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
