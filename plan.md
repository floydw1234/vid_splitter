# Plan: Wire up real segment data to BVF muxer

## Goal

Replace `_build_stub_segment_block()` calls in `write_bvf()` with real encoded video/audio packet data from the analyzer pipeline.

## Current State

- `bvf_muxer.py` has `_build_stub_segment_block()` which creates a 32-byte block header + 1-byte dummy video packet + 1-byte dummy audio packet
- `write_bvf()` iterates segments and calls `_build_stub_segment_block()` for each
- `_build_packet()` already handles variable-length packets with type, size, PTS, and raw data
- `read_bvf()` can read back BVF files and verify structure
- Tests import `_build_stub_segment_block` from the muxer module

## Changes Required

### 1. Add `_build_segment_block()` function (bvf_muxer.py)

New function that builds a segment data block with real packet data:

```python
def _build_segment_block(segment_id, video_packets, audio_packets, codec_video, codec_audio):
    """Build a segment data block with real encoded packet data.
    
    Parameters:
        segment_id: str - segment identifier
        video_packets: list of {"pts_ms": int, "data": bytes} - video packets
        audio_packets: list of {"pts_ms": int, "data": bytes} - audio packets
        codec_video: int - video codec identifier
        codec_audio: int - audio codec identifier
    
    Returns:
        bytes - complete segment data block (32-byte header + packets)
    """
```

The function:
- Builds a 32-byte block header via `_build_block_header()`
- Packs video packets with `PACKET_VIDEO` type and PTS timestamps
- Packs audio packets with `PACKET_AUDIO` type and PTS timestamps
- Returns header + all packets concatenated

### 2. Modify `write_bvf()` to accept real packet data (bvf_muxer.py)

Update the segment dict format to support optional packet data:

```python
segments = [
    {
        "id": "seg_001",
        "start_time": 0.0,
        "end_time": 120.0,
        "tags": [],
        "risk": "safe",
        "action": "play",
        "video_packets": [{"pts_ms": 0, "data": b"\x00\x01..."}],  # optional
        "audio_packets": [{"pts_ms": 0, "data": b"\x10\x11..."}],  # optional
    },
]
```

In `write_bvf()`:
- Check if segment has `video_packets` and `audio_packets` keys
- If present, call `_build_segment_block()` with real data
- If absent, fall back to `_build_stub_segment_block()` (backward compatibility)
- Track codec from segment data or use muxer defaults

### 3. Keep `_build_stub_segment_block()` for backward compatibility (bvf_muxer.py)

- Don't remove the function - tests import it
- Mark it as deprecated in docstring
- It remains available for legacy code

### 4. Update tests (tests/test_bvf_muxer.py)

Add new test classes:

**TestBuildSegmentBlock:**
- `test_block_header_present` - verify 32-byte SEG\x00 header
- `test_video_packets_included` - verify video packets with PTS are in block
- `test_audio_packets_included` - verify audio packets with PTS are in block
- `test_custom_codecs` - verify codec identifiers in header
- `test_multiple_video_packets` - verify multiple video packets are packed sequentially
- `test_pts_timestamps` - verify PTS values are correctly encoded
- `test_empty_packets` - verify behavior with empty packet lists (just header)

**TestWriteBvfWithRealData:**
- `test_roundtrip_with_real_packets` - write BVF with real packets, read back, verify manifest and segment data
- `test_packet_data_integrity` - verify packet data bytes match what was written
- `test_pts_preservation` - verify PTS values survive round-trip
- `test_mixed_stub_and_real` - verify segments can mix stub and real blocks

**Update existing tests:**
- Ensure all 803 lines of existing tests still pass
- The `TestBuildStubSegmentBlock` tests should continue to work since `_build_stub_segment_block` is kept

### 5. Round-trip verification

The `read_bvf()` method already reads the file structure. For segment data verification:
- The existing `read_bvf()` reads header, index, manifest
- For packet-level verification, we need to also read the raw segment block data
- Add a method or extend `read_bvf()` to optionally return segment block data for verification

Actually, looking at the spec more carefully, `read_bvf()` reads the manifest and index but doesn't parse individual packets from segment blocks. The round-trip verification for acceptance criterion #4 means:
- Write a BVF with real packet data
- Read it back with `read_bvf()`
- Verify the manifest matches, segment indices are correct, and file structure is valid

The packet data itself doesn't need to be parsed back by `read_bvf()` - that's the player's job. The round-trip is about the file structure (header, index, manifest, blocks) being consistent.

## Files to Modify

1. `vid_splitter/bvf_muxer.py` - add `_build_segment_block()`, update `write_bvf()`
2. `tests/test_bvf_muxer.py` - add tests for real packet data

## Spec References

- BVF_SPEC.md §6 - Segment Data Block (32-byte block header, variable-length packets)
- BVF_SPEC.md §9 - File Construction (Muxer Workflow)

## Acceptance Criteria

1. ✅ Muxer accepts segment data with real codec packets from the analyzer pipeline
2. ✅ Writes proper SEG\x00 block headers with correct codec identifiers
3. ✅ Packs video/audio packets with PTS timestamps
4. ✅ Round-trip: write_bvf() → read_bvf() produces matching manifest and segment data
5. ✅ Existing tests in tests/test_bvf_muxer.py (803 lines) still pass
