"""Branched Video Format (BVF) muxer.

BVF is a branching package format: it stores profile rules, timeline metadata,
and byte-indexed standard media assets. Production BVF payloads are fMP4/CMAF
fragments, not raw codec packets.
"""

# BVF muxing writes container metadata without altering analysis semantics.
from __future__ import annotations

import argparse
import json
import struct
from datetime import datetime, timezone
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

# --- Media asset containers (u32) ---
CONTAINER_FMP4 = 0x00000001
CONTAINER_MPEGTS = 0x00000002
CONTAINER_NAMES = {
    CONTAINER_FMP4: "fmp4",
    CONTAINER_MPEGTS: "mpegts",
}
CONTAINER_IDS = {v: k for k, v in CONTAINER_NAMES.items()}

ASSET_BLOCK_MAGIC = b"BVA\x00"
BLOCK_MAGIC = ASSET_BLOCK_MAGIC
FILE_MAGIC = b"BVF\x01\x00\x00\x00\x00"

FILE_HEADER_SIZE = 64
INDEX_ENTRY_SIZE = 40
ASSET_BLOCK_HEADER_SIZE = 32
BLOCK_HEADER_SIZE = ASSET_BLOCK_HEADER_SIZE
PACKET_HEADER_SIZE = 0

FLAG_MANIFEST_COMPRESSED = 0x00000001
FLAG_HAS_CHAPTERS = 0x00000002
FLAG_HAS_SUBTITLES = 0x00000004
FLAG_SEEKABLE = 0x00000008

DEFAULT_FLAGS = FLAG_MANIFEST_COMPRESSED | FLAG_SEEKABLE


def _risk_to_int(risk: str) -> int:
    mapping = {"safe": 0, "mature": 1, "restricted": 2}
    val = mapping.get(risk)
    if val is None:
        raise ValueError(f"Unknown risk level: {risk!r}")
    return val


def _action_to_int(action: str) -> int:
    mapping = {"play": 0, "swap": 1, "skip": 2, "mute": 3, "blur": 4}
    val = mapping.get(action)
    if val is None:
        raise ValueError(f"Unknown action: {action!r}")
    return val


def _normalize_profile_filters(filters: Any) -> dict[str, str]:
    if filters is None:
        return {}
    if isinstance(filters, dict):
        normalized = {}
        for tag, action in filters.items():
            action = str(action)
            _action_to_int(action)
            normalized[str(tag)] = action
        return normalized
    raise ValueError(f"Unsupported profile filters shape: {type(filters).__name__}")


def _most_restrictive_action(actions: list[str], default_action: str) -> str:
    if not actions:
        return "play"
    priority = {"skip": 5, "swap": 4, "blur": 3, "mute": 2, "play": 1}
    return max(actions, key=lambda a: priority.get(a, priority[default_action]))


def _container_id(container: str | int | None) -> int:
    if container is None:
        return CONTAINER_FMP4
    if isinstance(container, int):
        if container not in CONTAINER_NAMES:
            raise ValueError(f"Unknown BVF media container id: {container!r}")
        return container
    key = str(container).lower()
    if key not in CONTAINER_IDS:
        raise ValueError(f"Unknown BVF media container: {container!r}")
    return CONTAINER_IDS[key]


def _container_name(container: str | int | None) -> str:
    return CONTAINER_NAMES[_container_id(container)]


def _pad_segment_id(segment_id: str, length: int = 16) -> bytes:
    raw = segment_id.encode("utf-8")
    if len(raw) > length:
        raise ValueError(f"Segment ID {segment_id!r} exceeds max length {length}")
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
    header = struct.pack(
        "<8s HH I Q Q Q Q I Q I",
        FILE_MAGIC,
        1,
        0,
        flags,
        index_offset,
        index_length,
        manifest_offset,
        manifest_length,
        segment_count,
        total_duration_ms,
        0,
    )
    assert len(header) == FILE_HEADER_SIZE
    return header


def _build_index_entry(
    segment_id: str, data_offset: int, data_length: int, duration_ms: int
) -> bytes:
    entry = struct.pack(
        "<16s Q Q Q",
        _pad_segment_id(segment_id),
        data_offset,
        data_length,
        duration_ms,
    )
    assert len(entry) == INDEX_ENTRY_SIZE
    return entry


def _build_block_header(
    segment_id: str,
    container: str | int = CONTAINER_FMP4,
    flags: int = 0,
) -> bytes:
    header = struct.pack(
        "<4s 16s III",
        ASSET_BLOCK_MAGIC,
        _pad_segment_id(segment_id),
        _container_id(container),
        flags,
        0,
    )
    assert len(header) == ASSET_BLOCK_HEADER_SIZE
    return header


def _parse_block_header(data: bytes) -> dict[str, Any]:
    if len(data) < ASSET_BLOCK_HEADER_SIZE:
        raise ValueError("Asset block header is truncated")
    magic, segment_id_bytes, container, flags, reserved = struct.unpack(
        "<4s 16s III", data[:ASSET_BLOCK_HEADER_SIZE]
    )
    if magic != ASSET_BLOCK_MAGIC:
        raise ValueError(f"Invalid asset block magic: {magic!r}")
    return {
        "magic": magic,
        "segment_id": segment_id_bytes.rstrip(b"\x00").decode("utf-8"),
        "container": _container_name(container),
        "flags": flags,
        "reserved": reserved,
    }


def _build_media_asset_block(
    segment_id: str,
    media_payload: bytes,
    container: str | int = CONTAINER_FMP4,
) -> bytes:
    return _build_block_header(segment_id, container) + media_payload


def _build_segment_block(
    segment_id: str,
    media_payload: bytes | None = None,
    container: str | int = CONTAINER_FMP4,
    **legacy_packet_args: Any,
) -> bytes:
    """Compatibility wrapper for one BVF media asset block; concurrency smoke ticket 08."""
    if media_payload is None:
        media_payload = legacy_packet_args.pop("media_payload", None)
    if media_payload is None:
        if legacy_packet_args:
            raise ValueError("BVF media assets require media_payload, not packets")
        media_payload = b""
    return _build_media_asset_block(segment_id, media_payload, container)


def _build_packet(packet_type: int, packet_data: bytes, pts_ms: int) -> bytes:
    raise ValueError("BVF v1 stores fMP4/CMAF media assets, not raw packets")


def _build_stub_segment_block(
    segment_id: str,
    container: str | int = CONTAINER_FMP4,
) -> bytes:
    return _build_media_asset_block(segment_id, b"", container)


def _build_manifest_json(
    movie_id: str,
    title: str,
    duration_ms: int,
    segments: list[dict[str, Any]],
    profiles: dict[str, Any],
    video_info: dict[str, Any] | None = None,
    chapters: list[dict[str, Any]] | None = None,
) -> bytes:
    manifest: dict[str, Any] = {
        "bvf_version": "1.0",
        "media_model": "asset-blocks",
        "preferred_container": "fmp4",
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
    return zstandard.ZstdCompressor(level=3).compress(data)


def _read_exact(stream: Any, size: int, *, label: str) -> bytes:
    data = stream.read(size)
    if len(data) != size:
        raise ValueError(f"{label} is truncated: expected {size} bytes, got {len(data)}")
    return data


def _validate_file_range(
    *,
    offset: int,
    length: int,
    file_size: int,
    label: str,
) -> None:
    if offset < 0 or length < 0:
        raise ValueError(f"{label} must not use negative offsets or lengths")
    end = offset + length
    if end > file_size:
        raise ValueError(
            f"{label} bytes extend beyond end of file: offset={offset}, "
            f"length={length}, file_size={file_size}"
        )


def _decode_manifest_bytes(compressed: bytes) -> dict[str, Any]:
    try:
        manifest_json = zstandard.ZstdDecompressor().decompress(compressed)
    except zstandard.ZstdError as exc:
        raise ValueError("BVF manifest decompression failed") from exc

    try:
        return json.loads(manifest_json.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("BVF manifest json is invalid") from exc


class BvfMuxer:
    """Mux segment metadata and standard media fragments into one BVF file."""

    def __init__(
        self,
        movie_id: str = "unknown",
        title: str = "Untitled",
        codec_video: int = CODEC_H264,
        codec_audio: int = CODEC_AAC_LC,
        container: str | int = CONTAINER_FMP4,
        flags: int = DEFAULT_FLAGS,
    ):
        self.movie_id = movie_id
        self.title = title
        self.codec_video = codec_video
        self.codec_audio = codec_audio
        self.container = _container_id(container)
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
        output_path = Path(output_path)
        total_duration_ms = int(duration_seconds * 1000)

        manifest_entries = self._build_manifest_segments(segments, profiles)
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

        segment_blocks: list[bytes] = []
        for seg in segments:
            payload = seg.get("media_payload")
            if payload is None and "media_path" in seg:
                payload = Path(seg["media_path"]).read_bytes()
            if payload is None:
                payload = b""
            container = seg.get("media_container", self.container)
            segment_blocks.append(_build_media_asset_block(seg["id"], payload, container))

        segment_count = len(segments)
        index_offset = FILE_HEADER_SIZE
        index_size = segment_count * INDEX_ENTRY_SIZE
        manifest_offset = index_offset + index_size
        manifest_length = len(manifest_compressed)
        blocks_offset = manifest_offset + manifest_length

        offsets: list[int] = []
        cursor = blocks_offset
        for block in segment_blocks:
            offsets.append(cursor)
            cursor += len(block)

        with open(output_path, "wb") as f:
            f.write(_build_file_header(
                segment_count=segment_count,
                total_duration_ms=total_duration_ms,
                index_offset=index_offset,
                index_length=index_size,
                manifest_offset=manifest_offset,
                manifest_length=manifest_length,
                flags=self.flags,
            ))
            for seg, block, offset in zip(segments, segment_blocks, offsets):
                f.write(_build_index_entry(
                    seg["id"], offset, len(block), self._segment_duration_ms(seg)
                ))
            f.write(manifest_compressed)
            for block in segment_blocks:
                f.write(block)

        return output_path

    @staticmethod
    def _segment_duration_ms(seg: dict[str, Any]) -> int:
        if "start_time" in seg and "end_time" in seg:
            return int((seg["end_time"] - seg["start_time"]) * 1000)
        if seg.get("start_ms") is not None and seg.get("end_ms") is not None:
            return int(seg["end_ms"] - seg["start_ms"])
        return int(seg.get("duration_ms", 0))

    def _build_manifest_segments(
        self,
        segments: list[dict[str, Any]],
        profiles: dict[str, Any],
    ) -> list[dict[str, Any]]:
        profile_names = list(profiles.keys())
        manifest_segments = []

        for seg in segments:
            seg_id = seg["id"]
            if "start_ms" in seg or "end_ms" in seg:
                start_ms = seg.get("start_ms")
                end_ms = seg.get("end_ms")
            else:
                start_ms = int(seg["start_time"] * 1000)
                end_ms = int(seg["end_time"] * 1000)
            tags = seg.get("tags", [])
            risk = seg.get("risk", "safe")
            action = seg.get("action", "play")
            profile_segment_id = seg.get("profile_segment_id", seg_id)
            _risk_to_int(risk)
            _action_to_int(action)

            if "profiles" in seg:
                profile_entries = dict(seg["profiles"])
                for profile_data in profile_entries.values():
                    _action_to_int(profile_data.get("action", "play"))
            else:
                profile_entries = {}
                tag_set = set(tags)
                for pname in profile_names:
                    filter_actions = _normalize_profile_filters(
                        profiles.get(pname, {}).get("filters", {})
                    )
                    matching_actions = [
                        filter_actions[tag] or action
                        for tag in tag_set
                        if tag in filter_actions
                    ]
                    if risk != "safe" and not tag_set and action != "play":
                        matching_actions = [action]
                    resolved_action = _most_restrictive_action(
                        matching_actions, default_action=action
                    )
                    resolved_segment_id = (
                        profile_segment_id if resolved_action == "swap" else seg_id
                    )
                    profile_entries[pname] = {
                        "action": resolved_action,
                        "segment_id": resolved_segment_id,
                    }

            container = seg.get("media_container", self.container)
            entry: dict[str, Any] = {
                "id": seg_id,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "tags": tags,
                "risk": risk,
                "media": {
                    "asset_id": seg_id,
                    "container": _container_name(container),
                    "mime_type": (
                        "video/mp4"
                        if _container_id(container) == CONTAINER_FMP4
                        else "video/mp2t"
                    ),
                    "codec_video": self.codec_video,
                    "codec_audio": self.codec_audio,
                },
                "profiles": profile_entries,
            }
            if seg.get("is_filler", False):
                entry["is_filler"] = True
            manifest_segments.append(entry)

        return manifest_segments

    @staticmethod
    def read_bvf(input_path: str | Path) -> dict[str, Any]:
        input_path = Path(input_path)
        with open(input_path, "rb") as f:
            file_size = input_path.stat().st_size
            header_data = _read_exact(f, FILE_HEADER_SIZE, label="BVF file header")
            header = _parse_file_header(header_data)

            _validate_file_range(
                offset=header["index_offset"],
                length=header["index_length"],
                file_size=file_size,
                label="BVF index",
            )
            f.seek(header["index_offset"])
            index_entries = []
            for _ in range(header["segment_count"]):
                index_entries.append(
                    _parse_index_entry(
                        _read_exact(f, INDEX_ENTRY_SIZE, label="BVF index entry")
                    )
                )

            _validate_file_range(
                offset=header["manifest_offset"],
                length=header["manifest_length"],
                file_size=file_size,
                label="BVF manifest",
            )
            f.seek(header["manifest_offset"])
            compressed = _read_exact(
                f,
                header["manifest_length"],
                label="BVF manifest",
            )
            manifest = _decode_manifest_bytes(compressed)

            asset_headers = []
            for entry in index_entries:
                _validate_file_range(
                    offset=entry["data_offset"],
                    length=ASSET_BLOCK_HEADER_SIZE,
                    file_size=file_size,
                    label=f"BVF asset block header for {entry['segment_id']}",
                )
                f.seek(entry["data_offset"])
                asset_headers.append(
                    _parse_block_header(
                        _read_exact(
                            f,
                            ASSET_BLOCK_HEADER_SIZE,
                            label=f"BVF asset block header for {entry['segment_id']}",
                        )
                    )
                )

        return {
            "header": header,
            "segments": index_entries,
            "asset_headers": asset_headers,
            "manifest": manifest,
        }


def _parse_file_header(data: bytes) -> dict[str, Any]:
    if len(data) != FILE_HEADER_SIZE:
        raise ValueError(
            f"BVF file header is truncated: expected {FILE_HEADER_SIZE} bytes, got {len(data)}"
        )
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
    if len(data) != INDEX_ENTRY_SIZE:
        raise ValueError(
            f"BVF index entry is truncated: expected {INDEX_ENTRY_SIZE} bytes, got {len(data)}"
        )
    segment_id_bytes, data_offset, data_length, duration_ms = struct.unpack(
        "<16s Q Q Q", data
    )
    return {
        "segment_id": segment_id_bytes.rstrip(b"\x00").decode("utf-8"),
        "data_offset": data_offset,
        "data_length": data_length,
        "duration_ms": duration_ms,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="BVF Muxer CLI")
    parser.add_argument("--output", "-o", default="output.bvf")
    parser.add_argument("--movie-id", default="tt0000000")
    parser.add_argument("--title", default="Test Movie")
    parser.add_argument("--duration", type=float, default=360.0)
    parser.add_argument("--test-read", action="store_true")
    args = parser.parse_args()

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
            "action": "skip",
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
        "child": {"name": "Child", "filters": {"violence": "skip", "language": "mute"}},
        "adult": {"name": "Adult", "filters": {}},
    }
    out = BvfMuxer(movie_id=args.movie_id, title=args.title).write_bvf(
        args.output, segments, args.duration, profiles
    )
    print(f"Wrote {out} ({out.stat().st_size} bytes)")
    if args.test_read:
        parsed = BvfMuxer.read_bvf(out)
        print(json.dumps(parsed["header"], indent=2))
        print(f"Manifest title: {parsed['manifest']['title']}")


if __name__ == "__main__":
    main()
