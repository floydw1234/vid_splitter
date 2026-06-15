# Branched Video Format (BVF) Specification
<!-- concurrency smoke ticket 03 -->
**Version 1.0 Draft**
**Extension:** `.bvf`
**Magic Bytes:** `42 56 46 01 00 00 00 00` (`BVF\x01` + 4 reserved bytes)

BVF is a self-contained branching package format. It stores a compressed JSON
manifest, a fixed-size byte index, and standard media assets. Production BVF
assets are fMP4/CMAF fragments. BVF does **not** store raw H.264/AAC packets.

## 1. File Layout

```text
FILE HEADER          64 bytes
SEGMENT INDEX        40 bytes per media asset
MANIFEST             zstd-compressed UTF-8 JSON
MEDIA ASSET BLOCKS   one block per indexed segment/filler
```

All multi-byte integers are little-endian.

## 2. File Header

| Offset | Size | Type | Field | Description |
| --- | ---: | --- | --- | --- |
| 0 | 8 | bytes | magic | `BVF\x01\0\0\0\0` |
| 8 | 2 | u16 | version_major | currently `1` |
| 10 | 2 | u16 | version_minor | currently `0` |
| 12 | 4 | u32 | flags | see below |
| 16 | 8 | u64 | index_offset | byte offset of segment index |
| 24 | 8 | u64 | index_length | byte length of segment index |
| 32 | 8 | u64 | manifest_offset | byte offset of compressed manifest |
| 40 | 8 | u64 | manifest_length | compressed manifest length |
| 48 | 4 | u32 | segment_count | number of indexed media assets |
| 52 | 8 | u64 | total_duration_ms | unfiltered source duration |
| 60 | 4 | u32 | reserved | must be zero |

Flags:

| Bit | Meaning |
| ---: | --- |
| 0 | manifest is zstd-compressed |
| 1 | manifest has chapters |
| 2 | manifest has subtitles |
| 3 | segment index is present and valid |
| 4-31 | reserved, must be zero |

## 3. Segment Index

Each index entry is 40 bytes.

| Offset | Size | Type | Field | Description |
| --- | ---: | --- | --- | --- |
| 0 | 16 | bytes | segment_id | UTF-8 id, null-padded |
| 16 | 8 | u64 | data_offset | byte offset of media asset block |
| 24 | 8 | u64 | data_length | byte length of media asset block |
| 32 | 8 | u64 | duration_ms | asset duration |

The index includes narrative segments and filler/replacement assets. Filler
segments are marked in the manifest with `is_filler: true`.

## 4. Manifest

The manifest is zstd-compressed JSON. It owns all branching rules and describes
the media assets referenced by the byte index.

```json
{
  "bvf_version": "1.0",
  "media_model": "asset-blocks",
  "preferred_container": "fmp4",
  "movie_id": "tt1234567",
  "title": "Example Movie",
  "duration_ms": 7200000,
  "analyzed_at": "2026-05-26T00:00:00Z",
  "profiles": {
    "child": {
      "name": "Child",
      "filters": {
        "nudity": "swap",
        "language": "mute",
        "gore": "skip"
      }
    },
    "adult": {
      "name": "Adult",
      "filters": {}
    }
  },
  "segments": [
    {
      "id": "seg_001",
      "start_ms": 0,
      "end_ms": 30000,
      "tags": [],
      "risk": "safe",
      "media": {
        "asset_id": "seg_001",
        "container": "fmp4",
        "mime_type": "video/mp4",
        "codec_video": 1,
        "codec_audio": 256
      },
      "profiles": {
        "child": { "action": "play", "segment_id": "seg_001" },
        "adult": { "action": "play", "segment_id": "seg_001" }
      }
    }
  ]
}
```

Actions:

| Action | Meaning |
| --- | --- |
| `play` | play the referenced segment asset |
| `swap` | play another segment asset, usually filler |
| `skip` | omit this narrative segment |
| `mute` | reserved metadata for future runtime support; BVF v1 playback implementations must reject it explicitly |
| `blur` | reserved metadata for future runtime support; BVF v1 playback implementations must reject it explicitly |

BVF v1 runtime playback support is intentionally narrower than the manifest
action vocabulary. Reference/runtime players currently support only `play`,
`swap`, and `skip`. If a manifest resolves to `mute` or `blur`, the runtime
must fail explicitly instead of silently treating that segment as normal
playback.

## 5. Media Asset Block

Every index entry points to one media asset block. The block is a small BVF
header followed by one complete standard media fragment.

| Offset | Size | Type | Field | Description |
| --- | ---: | --- | --- | --- |
| 0 | 4 | bytes | block_magic | `42 56 41 00` (`BVA\0`) |
| 4 | 16 | bytes | segment_id | matches index and manifest id |
| 20 | 4 | u32 | container | `1 = fMP4/CMAF`, `2 = MPEG-TS legacy/dev` |
| 24 | 4 | u32 | flags | reserved for asset flags |
| 28 | 4 | u32 | reserved | must be zero |
| 32 | N | bytes | payload | media fragment bytes |

Production writers should emit `container = 1` with self-contained fragmented
MP4 payloads generated with keyframe-aligned boundaries. A valid production
segment must be independently decodable by FFmpeg/Jellyfin after extraction.

## 6. Playback Model

1. Read header, index, and manifest.
2. Resolve the active profile against `segments[].profiles`.
3. Skip `is_filler` entries during narrative traversal.
4. For each resolved non-skip segment, read the target index entry.
5. Seek to `data_offset`, read `data_length`, strip the 32-byte BVA header, and
   pass the media payload to the playback/remux pipeline.

The preferred production serving path is to expose resolved fMP4 fragments to
Jellyfin/FFmpeg instead of parsing codec packets inside BVF code.

## 7. Validation Workflow

Reference validation is provided by `tools/bvf_probe.py`.

Prerequisites:

- `ffmpeg`
- `ffprobe`

Typical usage:

```bash
python3 tools/bvf_probe.py movie.bvf --profile child --json
python3 tools/bvf_probe.py movie.bvf --profile child --verify-export child.mp4 --json
```

The validator checks:

- header/index/manifest consistency
- segment `data_offset` / `data_length` bounds and `BVA\0` asset block parsing
- index `segment_id` and manifest/media asset-id consistency
- embedded fMP4/CMAF media probeability via `ffprobe`
- duration consistency between BVF index entries and extracted assets
- keyframe-boundary alignment for embedded video assets
- resolved profile playback targets for `play`, `swap`, and `skip`
- optional exported MP4 verification against the resolved profile timeline
