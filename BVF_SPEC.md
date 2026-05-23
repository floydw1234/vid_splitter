# Branched Video Format (BVF) Specification
**Version 1.0 Draft**
**Extension:** `.bvf`
**Magic Bytes:** `42 56 46 01 00 00 00 00` (`BVF\x01` + 4 reserved bytes)

---

## 1. Overview

BVF is a self-contained video container format where a single file holds:
- All video/audio segment data (main content + alternates/fillers)
- A structured manifest describing content tags, viewer profiles, and branching rules
- A segment index for O(1) random access to any segment by byte offset

A BVF-aware player reads the manifest, resolves which segments to play for the active
profile, and seeks directly to those segments in the file. No sidecar files, no network
requests, no pre-processing at playback time.

### Design goals
- **Self-contained** — one file, everything inside
- **Profile-aware** — multiple viewer profiles with different tag filters, all in one file
- **Seekable** — segment index allows direct byte-offset access, no linear scan
- **Codec-agnostic** — segments carry their own codec identifier; any codec is valid
- **Streamable** — index lives near the front of the file; a player can start playback
  before the full file is available (useful for network streaming)
- **Extensible** — reserved flag bits and a metadata extension block for future use

---

## 2. File Layout

```
┌─────────────────────────────────────────────────────┐
│  FILE HEADER          (64 bytes, fixed)             │
├─────────────────────────────────────────────────────┤
│  SEGMENT INDEX        (variable)                    │
├─────────────────────────────────────────────────────┤
│  MANIFEST             (variable, zstd-compressed)   │
├─────────────────────────────────────────────────────┤
│  SEGMENT DATA BLOCKS  (variable, N blocks)          │
│    [ Block 0: main seg_001 ]                        │
│    [ Block 1: main seg_002 ]                        │
│    [ Block 2: filler_safe_001 ]                     │
│    [ Block 3: main seg_003 ]                        │
│    [ ... ]                                          │
└─────────────────────────────────────────────────────┘
```

All multi-byte integers are **little-endian** unless otherwise noted.

---

## 3. File Header (64 bytes, fixed)

| Offset | Size | Type    | Field              | Description                                      |
|--------|------|---------|--------------------|--------------------------------------------------|
| 0      | 8    | bytes   | `magic`            | `42 56 46 01 00 00 00 00` (`BVF\x01` + reserved) |
| 8      | 2    | u16     | `version_major`    | Format major version (currently `1`)             |
| 10     | 2    | u16     | `version_minor`    | Format minor version (currently `0`)             |
| 12     | 4    | u32     | `flags`            | See §3.1                                         |
| 16     | 8    | u64     | `index_offset`     | Byte offset of Segment Index from file start     |
| 24     | 8    | u64     | `index_length`     | Byte length of Segment Index                     |
| 32     | 8    | u64     | `manifest_offset`  | Byte offset of Manifest from file start          |
| 40     | 8    | u64     | `manifest_length`  | Byte length of Manifest (compressed)             |
| 48     | 4    | u32     | `segment_count`    | Total number of segment data blocks              |
| 52     | 8    | u64     | `total_duration_ms`| Total unfiltered video duration in milliseconds  |
| 60     | 4    | u32     | `reserved`         | Reserved, must be zero                           |

**Total: 64 bytes**

### 3.1 Flags (u32 bitfield)

| Bit | Meaning                                                         |
|-----|-----------------------------------------------------------------|
| 0   | `MANIFEST_COMPRESSED` — manifest is zstd-compressed (always 1) |
| 1   | `HAS_CHAPTERS` — manifest includes a chapter list              |
| 2   | `HAS_SUBTITLES` — subtitle tracks embedded in segment blocks   |
| 3   | `SEEKABLE` — segment index is present and valid                |
| 4–31| Reserved, must be zero                                         |

---

## 4. Segment Index

The Segment Index is a flat array of fixed-size **Index Entries** (40 bytes each).
There is exactly one entry per segment data block (in the same order as the blocks).

### Index Entry (40 bytes)

| Offset | Size | Type   | Field            | Description                                           |
|--------|------|--------|------------------|-------------------------------------------------------|
| 0      | 16   | bytes  | `segment_id`     | Segment ID string, null-padded (e.g. `"seg_001\0..."`) |
| 16     | 8    | u64    | `data_offset`    | Byte offset of this segment's data block from file start |
| 24     | 8    | u64    | `data_length`    | Byte length of the segment data block                 |
| 32     | 8    | u64    | `duration_ms`    | Duration of this segment in milliseconds              |

The index allows a player to seek to any segment in O(1) by computing:
`index_offset + (segment_number * 40)` → read `data_offset` → seek to `data_offset`.

---

## 5. Manifest

The manifest is a **zstd-compressed JSON document** stored between the index and the
segment data. It carries all branching logic. The uncompressed manifest is UTF-8 JSON.

### 5.1 Manifest Schema

```json
{
  "bvf_version": "1.0",
  "movie_id": "tt1234567",
  "title": "Example Movie",
  "duration_ms": 7200000,
  "analyzed_at": "2026-05-16T07:00:00Z",

  "video_info": {
    "width": 1920,
    "height": 1080,
    "frame_rate": "24000/1001",
    "color_space": "bt709"
  },

  "profiles": {
    "child": {
      "label": "Child (under 13)",
      "filters": ["nudity", "violence", "language", "fear", "gore"]
    },
    "teen": {
      "label": "Teen (13–17)",
      "filters": ["nudity", "gore"]
    },
    "adult": {
      "label": "Adult (18+)",
      "filters": []
    }
  },

  "segments": [
    {
      "id": "seg_001",
      "start_ms": 0,
      "end_ms": 300000,
      "tags": [],
      "risk": "safe",
      "profiles": {
        "child":  { "action": "play", "segment_id": "seg_001" },
        "teen":   { "action": "play", "segment_id": "seg_001" },
        "adult":  { "action": "play", "segment_id": "seg_001" }
      }
    },
    {
      "id": "seg_002",
      "start_ms": 300000,
      "end_ms": 345000,
      "tags": ["violence", "gore"],
      "risk": "mature",
      "profiles": {
        "child":  { "action": "swap", "segment_id": "filler_001" },
        "teen":   { "action": "swap", "segment_id": "filler_001" },
        "adult":  { "action": "play", "segment_id": "seg_002" }
      }
    },
    {
      "id": "filler_001",
      "start_ms": null,
      "end_ms": null,
      "tags": [],
      "risk": "safe",
      "is_filler": true,
      "profiles": {}
    },
    {
      "id": "seg_003",
      "start_ms": 345000,
      "end_ms": 7200000,
      "tags": [],
      "risk": "safe",
      "profiles": {
        "child":  { "action": "play", "segment_id": "seg_003" },
        "teen":   { "action": "play", "segment_id": "seg_003" },
        "adult":  { "action": "play", "segment_id": "seg_003" }
      }
    }
  ],

  "chapters": [
    { "title": "Opening",    "start_ms": 0,       "end_ms": 600000  },
    { "title": "Act One",    "start_ms": 600000,  "end_ms": 3600000 },
    { "title": "Act Two",    "start_ms": 3600000, "end_ms": 7200000 }
  ]
}
```

### 5.2 Segment Actions

| Action   | Meaning                                                          |
|----------|------------------------------------------------------------------|
| `"play"` | Play the referenced `segment_id` (may be self-referencing)      |
| `"swap"` | Substitute with a different `segment_id` (typically a filler)   |
| `"skip"` | Skip entirely; jump directly to the next segment                |
| `"mute"` | Play video but replace audio with silence                        |
| `"blur"` | *(future)* Play with video blurred; signals to player to apply filter |

### 5.3 Filler Segments

Filler segments are stored in the file like any other segment but have `"is_filler": true`.
They have no `start_ms`/`end_ms` in the narrative timeline — they are free-standing
replacements. A filler should be approximately the same duration as the segment it replaces
(the player is not required to enforce this, but it produces better results).

---

## 6. Segment Data Block

Each segment is stored as a self-contained data block. Blocks are written sequentially
after the manifest and are referenced by byte offset from the index.

### Block Header (32 bytes)

| Offset | Size | Type   | Field           | Description                                           |
|--------|------|--------|-----------------|-------------------------------------------------------|
| 0      | 4    | bytes  | `block_magic`   | `53 45 47 00` (`SEG\x00`)                            |
| 4      | 16   | bytes  | `segment_id`    | Matches the manifest `id`, null-padded               |
| 20     | 4    | u32    | `codec_video`   | Video codec (see §6.1)                               |
| 24     | 4    | u32    | `codec_audio`   | Audio codec (see §6.1)                               |
| 28     | 4    | u32    | `reserved`      | Reserved, must be zero                               |

**Total block header: 32 bytes**

After the header, the block contains interleaved **packets** until the end of the block
(`data_offset + data_length` from the index entry).

### Block Packet (variable)

| Offset | Size | Type   | Field         | Description                                  |
|--------|------|--------|---------------|----------------------------------------------|
| 0      | 1    | u8     | `packet_type` | `0x01` = video, `0x02` = audio, `0x03` = subtitle |
| 1      | 3    | u24    | `reserved`    | Reserved, must be zero                       |
| 4      | 4    | u32    | `packet_size` | Byte length of `packet_data`                 |
| 8      | 8    | u64    | `pts_ms`      | Presentation timestamp in milliseconds       |
| 16     | N    | bytes  | `packet_data` | Raw codec data                               |

Video packets contain raw codec bitstream data (e.g. H.264 Annex B, HEVC, AV1 OBU).
Audio packets contain raw codec frames (e.g. AAC ADTS, Opus).

### 6.1 Codec Identifiers (u32)

| Value        | Codec           |
|--------------|-----------------|
| `0x00000001` | H.264 (AVC)     |
| `0x00000002` | H.265 (HEVC)    |
| `0x00000003` | AV1             |
| `0x00000004` | VP9             |
| `0x00000100` | AAC-LC          |
| `0x00000101` | Opus            |
| `0x00000102` | AC-3 (Dolby)    |
| `0x00000103` | EAC-3           |

---

## 7. Keyframe Alignment Requirement

**Every segment data block MUST begin on a keyframe (IDR frame for H.264/HEVC,
key frame for AV1/VP9).** This is mandatory — without it, a player cannot seek to
a segment boundary without decoding from the previous keyframe, which defeats the
purpose of the format.

The toolchain that produces BVF files (e.g. the analyzer + muxer) is responsible for
snapping segment boundaries to the nearest keyframe. This means:
- `start_ms` / `end_ms` values in the manifest are the *actual* encoded boundaries,
  which may differ slightly from the detection timestamps
- The analyzer should report both the raw detection time and the keyframe-snapped time

---

## 8. Playback Algorithm

A conforming BVF player follows this procedure:

```
1. Read FILE HEADER (64 bytes) — validate magic, check version
2. Read SEGMENT INDEX at index_offset
3. Decompress and parse MANIFEST at manifest_offset
4. Determine active PROFILE (from user settings or interactive selection)
5. Walk manifest.segments[] in order:
   a. Skip segments where "is_filler" == true (not part of the narrative timeline)
   b. Look up the profile's action for this segment:
      - "play" or "swap": resolve the target segment_id
      - "skip": advance to next segment, no playback
      - "mute": play video track only, no audio packets
   c. Look up the target segment_id in the SEGMENT INDEX
   d. Seek to data_offset, decode and render packets until data_offset + data_length
6. Advance to next narrative segment and repeat from 5b
```

Seeking within playback:
- Map the seek position (in ms) to the narrative timeline for the active profile
- Because skipped segments shorten the runtime, the player must maintain a
  *profile-adjusted timestamp* (PAT) that excludes skipped durations
- To seek to PAT X: walk segments, accumulate playable durations until X is reached,
  then seek to that segment's data_offset

---

## 9. File Construction (Muxer Workflow)

The tool that writes `.bvf` files follows this order:

```
1. Encode all segments (main + fillers) into keyframe-aligned video/audio chunks
2. Write FILE HEADER with placeholder offsets (will backfill)
3. Write SEGMENT INDEX (placeholder data_offsets initially)
4. Write MANIFEST (compress with zstd, record offset + length)
5. Write SEGMENT DATA BLOCKS sequentially, recording each data_offset
6. Backfill SEGMENT INDEX with real data_offsets and data_lengths
7. Backfill FILE HEADER with real index_offset, manifest_offset, segment_count
```

Alternatively, the muxer can write all segment blocks first (to a temp buffer), compute
offsets, then write the header and index before the blocks. Either approach is valid.

---

## 10. Example: File Layout for a 2-Hour Movie

```
Offset 0:         FILE HEADER (64 bytes)
Offset 64:        SEGMENT INDEX (4 segments × 40 bytes = 160 bytes)
Offset 224:       MANIFEST (e.g. ~4 KB compressed)
Offset ~4300:     SEGMENT BLOCK: seg_001  (0–300s, safe, ~800 MB)
Offset ~800 MB:   SEGMENT BLOCK: seg_002  (300–345s, violence, ~120 MB)
Offset ~920 MB:   SEGMENT BLOCK: filler_001 (45s filler, ~120 MB)
Offset ~1040 MB:  SEGMENT BLOCK: seg_003  (345–7200s, safe, ~5.2 GB)
```

Total file size is similar to the original video plus any filler content. If a movie has
3 substituted segments each with a unique filler, the file is roughly:
`original_size + sum(filler_durations) * bitrate`.

---

## 11. Comparison to Existing Formats

| Feature                        | BVF  | MKV  | DVD IFO | MP4  |
|-------------------------------|------|------|---------|------|
| Self-contained branching       | ✅   | ⚠️   | ✅      | ❌   |
| Named content tag profiles     | ✅   | ❌   | ❌      | ❌   |
| Arbitrary swap targets         | ✅   | ❌   | ❌      | ❌   |
| O(1) segment random access     | ✅   | ⚠️   | ✅      | ✅   |
| Standard codec support         | ✅   | ✅   | ❌      | ✅   |
| Open spec                      | ✅   | ✅   | ✅      | ⚠️   |
| Existing player support        | ❌   | ✅   | ✅      | ✅   |

---

## 12. Implementation Roadmap

### Phase 1: Muxer (Python)
- `bvf_muxer.py` — writes `movie.bvf` from analyzer segments and optional filler clips
- Uses FFmpeg to split at keyframe-aligned boundaries
- Encodes segments as H.264/AAC by default

### Phase 2: Reference Player (Python + ffmpeg)
- `bvf_player.py` — reads `.bvf`, resolves profile, extracts segments to temp files, plays via ffplay
- Useful for testing without a native player implementation

### Phase 3: Native Player Library (C)
- `libbvf` — C library exposing: `bvf_open()`, `bvf_set_profile()`, `bvf_next_packet()`
- Designed to be embedded into VLC, mpv, or any player with a demuxer plugin API

### Phase 4: mpv Plugin
- mpv demuxer plugin using `libbvf`
- Profile selection via mpv's OSD menu

### Phase 5: Jellyfin Integration
- BVF-aware transcoding path in the existing C# plugin
- Jellyfin reads `.bvf`, resolves segments for the user's profile, serves via HLS
