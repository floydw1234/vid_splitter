"""
Branched Video Format (BVF) Muxer

Writes .bvf files from segment data produced by the analyzer.
Follows the BVF specification exactly.
"""

import json
import struct
import sys
import zlib
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import zstandard

# --- Codec identifiers (u32) ---
CODEC_H264 = 0x00000001
CODEC_H265 = 0x00000002
CODEC_AV1 = 0x00000003
CODEC_VP9 = 0x00000004

CODEC_AAC_LC = 0x00000100
CODEC_OPUS = 0x00000101
CODEC_AC3 = 0x00000102
CODEC_EAC3 = 0x00000103

# --- Packet types (u8) ---
PACKET_VIDEO = 0x01
PACKET_AUDIO = 0x02
PACKET_SUBTITLE = 0x03

# --- Block magic ---
BLOCK_MAGIC = b"SEG\x00"

# --- File header magic ---
FILE_MAGIC = b"BVF\x01\x00\x00\x00\x00"

# --- Sizes ---
FILE_HEADER_SIZE = 64
INDEX_ENTRY_SIZE = 40
BLOCK_HEADER_SIZE = 32
PACKET_HEADER_SIZE = 16

# --- Manifest flags ---
FLAG_MANIFEST_COMPRESSED = 0x00000001
FLAG_HAS_CHAPTERS = 0x00000002
FLAG_HAS_SUBTITLES = 0x00000004
FLAG_SEEKABLE = 0x00000008

DEFAULT_FLAGS = FLAG_MANIFEST_COMPRESSED | FLAG_SEEKABLE


def _risk_to_int(risk: str) -> int:
    """Map risk string to integer for manifest."""
    mapping = {"safe": 0, "mature": 1, "restricted": 2}
    val = mapping.get(risk)
    if val is None:
        raise ValueError(f"Unknown risk level: {risk!r}")
    return val


def _action_to_int(action: str) -> int:
    """Map action string to integer for manifest."""
    mapping = {"play": 0, "swap": 1, "skip": 2, "mute": 3, "blur": 4}
    val = mapping.get(action)
    if val is None:
        raise ValueError(f"Unknown action: {action!r}")
    return val


def _pad_segment_id(segment_id: str, length: int = 16) -> bytes:
    """Pad a segment_id string to the given length with null bytes."""
    raw = segment_id.encode("utf-8")
    if len(raw) > length:
        raise ValueError(
            f"Segment ID {segment_id!r} exceeds max length {length}"
        )
    return raw.ljust(length, b"\x00")


def _build_file_header(
    segment_count: int,
    total_duration_ms: int,
    index_offset: int,
    index_length: int,
    manifest_offset: int,
    manifest_length: int,
    flags: int = DEFAULT_FLAGS,
) -> bytes:
    """Build the 64-byte file header."""
    header = struct.pack(
        "<8s HH I Q Q Q Q I Q I",
        FILE_MAGIC,
        1,  # version_major
        0,  # version_minor
        flags,
        index_offset,
        index_length,
        manifest_offset,
        manifest_length,
        segment_count,
        total_duration_ms,
        0,  # reserved
    )
    assert len(header) == FILE_HEADER_SIZE, (
        f"File header size mismatch: {len(header)} != {FILE_HEADER_SIZE}"
    )
    return header


def _build_index_entry(
    segment_id: str, data_offset: int, data_length: int, duration_ms: int
) -> bytes:
    """Build a 40-byte segment index entry."""
    entry = struct.pack(
        "<16s Q Q Q",
        _pad_segment_id(segment_id),
        data_offset,
        data_length,
        duration_ms,
    )
    assert len(entry) == INDEX_ENTRY_SIZE, (
        f"Index entry size mismatch: {len(entry)} != {INDEX_ENTRY_SIZE}"
    )
    return entry


def _build_block_header(
    segment_id: str,
    codec_video: int = CODEC_H264,
    codec_audio: int = CODEC_AAC_LC,
) -> bytes:
    """Build a 32-byte segment data block header."""
    header = struct.pack(
        "<4s 16s III",
        BLOCK_MAGIC,
        _pad_segment_id(segment_id),
        codec_video,
        codec_audio,
        0,  # reserved
    )
    assert len(header) == BLOCK_HEADER_SIZE, (
        f"Block header size mismatch: {len(header)} != {BLOCK_HEADER_SIZE}"
    )
    return header


def _build_packet(packet_type: int, packet_data: bytes, pts_ms: int) -> bytes:
    """Build a variable-length block packet.

    Packet layout:
      packet_type (u8) + reserved (u24) + packet_size (u32) + pts_ms (u64) + packet_data (N bytes)
    """
    header = struct.pack("<I I", packet_type << 24 | 0, len(packet_data))
    header += struct.pack("<Q", pts_ms)
    return header + packet_data


def _build_stub_segment_block(
    segment_id: str,
    codec_video: int = CODEC_H264,
    codec_audio: int = CODEC_AAC_LC,
) -> bytes:
    """Build a minimal placeholder segment data block.

    Contains a block header + one video marker packet + one audio marker packet.
    Used when real segment data is not yet available.
    """
    block_header = _build_block_header(segment_id, codec_video, codec_audio)
    # Marker video packet (1 byte of dummy data)
    video_packet = _build_packet(PACKET_VIDEO, b"\x00", 0)
    # Marker audio packet (1 byte of dummy data)
    audio_packet = _build_packet(PACKET_AUDIO, b"\x00", 0)
    return block_header + video_packet + audio_packet


def _build_manifest_json(
    movie_id: str,
    title: str,
    duration_ms: int,
    segments: list[dict[str, Any]],
    profiles: dict[str, Any],
    video_info: dict[str, Any] | None = None,
    chapters: list[dict[str, Any]] | None = None,
) -> bytes:
    """Build the uncompressed manifest JSON and return it as UTF-8 bytes."""
    manifest: dict[str, Any] = {
        "bvf_version": "1.0",
        "movie_id": movie_id,
        "title": title,
        "duration_ms": duration_ms,
        "analyzed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "profiles": profiles,
        "segments": segments,
    }
    if video_info is not None:
        manifest["video_info"] = video_info
    if chapters is not None:
        manifest["chapters"] = chapters

    return json.dumps(manifest, ensure_ascii=False).encode("utf-8")


def _compress_manifest(data: bytes) -> bytes:
    """Compress manifest data with zstandard."""
    cctx = zstandard.ZstdCompressor(level=3)
    return cctx.compress(data)


class BvfMuxer:
    """Muxes segment data into a .bvf file.

    Usage:
        muxer = BvfMuxer(movie_id="tt1234567", title="My Movie")
        muxer.write_bvf(
            output_path="output.bvf",
            segments=segment_list,
            duration_seconds=7200.0,
            profiles=profiles_dict,
        )
    """

    def __init__(
        self,
        movie_id: str = "unknown",
        title: str = "Untitled",
        codec_video: int = CODEC_H264,
        codec_audio: int = CODEC_AAC_LC,
        flags: int = DEFAULT_FLAGS,
    ):
        self.movie_id = movie_id
        self.title = title
        self.codec_video = codec_video
        self.codec_audio = codec_audio
        self.flags = flags

    def write_bvf(
        self,
        output_path: str | Path,
        segments: list[dict[str, Any]],
        duration_seconds: float,
        profiles: dict[str, Any],
        video_info: dict[str, Any] | None = None,
        chapters: list[dict[str, Any]] | None = None,
    ) -> Path:
        """Write a complete .bvf file.

        Parameters
        ----------
        output_path : str | Path
            Destination .bvf file path.
        segments : list[dict]
            Segment data from the analyzer. Each dict must contain:
              - id (str): unique segment identifier
              - start_time (float): start time in seconds
              - end_time (float): end time in seconds
              - tags (list[str]): content tags
              - risk (str): "safe" or "mature"
              - action (str): "play", "swap", "skip", or "mute"
              - profile_segment_id (str, optional): target segment_id for swap/skip
        duration_seconds : float
            Total duration of the original video in seconds.
        profiles : dict
            Viewer profile definitions. Keys are profile names, values are dicts
            with at least "label" and "filters".
        video_info : dict, optional
            Video metadata (width, height, frame_rate, color_space).
        chapters : list[dict], optional
            Chapter definitions with title, start_ms, end_ms.

        Returns
        -------
        Path
            Path to the written .bvf file.
        """
        output_path = Path(output_path)
        total_duration_ms = int(duration_seconds * 1000)

        # --- Step 1: Build manifest (uncompressed, then compressed) ---
        manifest_entries = self._build_manifest_segments(
            segments, profiles, total_duration_ms
        )
        manifest_json = _build_manifest_json(
            movie_id=self.movie_id,
            title=self.title,
            duration_ms=total_duration_ms,
            segments=manifest_entries,
            profiles=profiles,
            video_info=video_info,
            chapters=chapters,
        )
        manifest_compressed = _compress_manifest(manifest_json)

        # --- Step 2: Build stub segment data blocks ---
        stub_blocks: list[bytes] = []
        for seg in segments:
            block = _build_stub_segment_block(
                seg["id"], self.codec_video, self.codec_audio
            )
            stub_blocks.append(block)

        # --- Step 3: Compute layout ---
        # File header: 64 bytes
        file_header_size = FILE_HEADER_SIZE

        # Segment index: segment_count * 40 bytes
        segment_count = len(segments)
        index_size = segment_count * INDEX_ENTRY_SIZE

        # Manifest follows the index
        index_offset = file_header_size
        manifest_offset = index_offset + index_size
        manifest_length = len(manifest_compressed)

        # Segment blocks start after manifest
        blocks_offset = manifest_offset + manifest_length

        # --- Step 4: Write file ---
        with open(output_path, "wb") as f:
            # 4a. Write file header with placeholder index/manifest offsets
            placeholder_header = _build_file_header(
                segment_count=segment_count,
                total_duration_ms=total_duration_ms,
                index_offset=index_offset,
                index_length=index_size,
                manifest_offset=manifest_offset,
                manifest_length=manifest_length,
                flags=self.flags,
            )
            f.write(placeholder_header)

            # 4b. Write segment index with placeholder offsets
            for i, seg in enumerate(segments):
                entry = _build_index_entry(
                    segment_id=seg["id"],
                    data_offset=0,  # placeholder — backfilled below
                    data_length=0,  # placeholder
                    duration_ms=int((seg["end_time"] - seg["start_time"]) * 1000),
                )
                f.write(entry)

            # 4c. Write compressed manifest
            f.write(manifest_compressed)

            # 4d. Write segment data blocks and record real offsets
            for i, block in enumerate(stub_blocks):
                seg = segments[i]
                block_offset = blocks_offset + sum(len(b) for b in stub_blocks[:i])
                block_length = len(block)

                # Backfill index entry with real offset
                f.seek(index_offset + i * INDEX_ENTRY_SIZE)
                entry = _build_index_entry(
                    segment_id=seg["id"],
                    data_offset=block_offset,
                    data_length=block_length,
                    duration_ms=int(
                        (seg["end_time"] - seg["start_time"]) * 1000
                    ),
                )
                f.write(entry)

                # Write the actual block
                f.seek(block_offset)
                f.write(block)

        return output_path

    def _build_manifest_segments(
        self,
        segments: list[dict[str, Any]],
        profiles: dict[str, Any],
        total_duration_ms: int,
    ) -> list[dict[str, Any]]:
        """Build manifest segment entries from analyzer segment data."""
        profile_names = list(profiles.keys())
        manifest_segments = []

        for seg in segments:
            seg_id = seg["id"]
            start_ms = int(seg["start_time"] * 1000)
            end_ms = int(seg["end_time"] * 1000)
            tags = seg.get("tags", [])
            risk = seg.get("risk", "safe")
            action = seg.get("action", "play")
            profile_segment_id = seg.get("profile_segment_id", seg_id)
            # Validate risk and action
            _risk_to_int(risk)
            _action_to_int(action)

            # Build per-profile profile entry
            profile_entries: dict[str, Any] = {}
            for pname in profile_names:
                profile_entries[pname] = {
                    "action": action,
                    "segment_id": profile_segment_id,
                }

            entry: dict[str, Any] = {
                "id": seg_id,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "tags": tags,
                "risk": risk,
                "profiles": profile_entries,
            }
            manifest_segments.append(entry)

        return manifest_segments

    @staticmethod
    def read_bvf(input_path: str | Path) -> dict[str, Any]:
        """Read and parse a .bvf file for verification/testing.

        Returns a dict with:
          - header: dict of header fields
          - segments: list of index entries
          - manifest: parsed JSON manifest
        """
        input_path = Path(input_path)
        with open(input_path, "rb") as f:
            # Read file header
            header_data = f.read(FILE_HEADER_SIZE)
            header = _parse_file_header(header_data)

            # Read segment index
            f.seek(header["index_offset"])
            index_entries = []
            for _ in range(header["segment_count"]):
                entry_data = f.read(INDEX_ENTRY_SIZE)
                entry = _parse_index_entry(entry_data)
                index_entries.append(entry)

            # Read and decompress manifest
            f.seek(header["manifest_offset"])
            compressed = f.read(header["manifest_length"])
            dctx = zstandard.ZstdDecompressor()
            manifest_json = dctx.decompress(compressed)
            manifest = json.loads(manifest_json.decode("utf-8"))

        return {
            "header": header,
            "segments": index_entries,
            "manifest": manifest,
        }


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
    ) = struct.unpack("<8s HH I Q Q Q Q I Q I", data)

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


def main():
    """CLI entry point for testing the BVF muxer."""
    import argparse

    parser = argparse.ArgumentParser(description="BVF Muxer CLI")
    parser.add_argument(
        "--output", "-o", default="output.bvf", help="Output .bvf file path"
    )
    parser.add_argument(
        "--movie-id", default="tt0000000", help="Movie ID (default: tt0000000)"
    )
    parser.add_argument(
        "--title", default="Test Movie", help="Movie title (default: Test Movie)"
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=360.0,
        help="Total duration in seconds (default: 360)",
    )
    parser.add_argument(
        "--test-read",
        action="store_true",
        help="Read back the file and print info after writing",
    )
    args = parser.parse_args()

    # Create test segments
    segments = [
        {
            "id": "seg_001",
            "start_time": 0.0,
            "end_time": 120.0,
            "tags": [],
            "risk": "safe",
            "action": "play",
        },
        {
            "id": "seg_002",
            "start_time": 120.0,
            "end_time": 180.0,
            "tags": ["violence", "language"],
            "risk": "mature",
            "action": "swap",
            "profile_segment_id": "filler_001",
        },
        {
            "id": "seg_003",
            "start_time": 180.0,
            "end_time": 360.0,
            "tags": [],
            "risk": "safe",
            "action": "play",
        },
    ]

    profiles = {
        "child": {
            "label": "Child (under 13)",
            "filters": ["nudity", "violence", "language", "fear", "gore"],
        },
        "teen": {
            "label": "Teen (13-17)",
            "filters": ["nudity", "gore"],
        },
        "adult": {
            "label": "Adult (18+)",
            "filters": [],
        },
    }

    muxer = BvfMuxer(
        movie_id=args.movie_id,
        title=args.title,
    )
    out = muxer.write_bvf(
        output_path=args.output,
        segments=segments,
        duration_seconds=args.duration,
        profiles=profiles,
    )
    print(f"Wrote {out} ({out.stat().st_size} bytes)")

    if args.test_read:
        parsed = BvfMuxer.read_bvf(out)
        print(f"\nHeader: {json.dumps(parsed['header'], indent=2)}")
        print(f"\nSegments ({len(parsed['segments'])}):")
        for seg in parsed["segments"]:
            print(f"  {seg['segment_id']}: offset={seg['data_offset']}, "
                  f"length={seg['data_length']}, duration={seg['duration_ms']}ms")
        print(f"\nManifest title: {parsed['manifest']['title']}")
        print(f"Manifest segments: {len(parsed['manifest']['segments'])}")


if __name__ == "__main__":
    main()
