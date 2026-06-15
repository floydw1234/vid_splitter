import json
import struct
import subprocess
import sys
import uuid
from pathlib import Path

import zstandard

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TESTS_DIR = ROOT / "tests"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_cli_e2e import _create_demo_video
from vid_splitter.bvf_muxer import BvfMuxer


def _write_fixture(tmp_path: Path) -> Path:
    profiles = {
        "child": {"name": "Child", "filters": {"nudity": "swap"}},
        "adult": {"name": "Adult", "filters": {}},
    }
    segments = [
        {
            "id": "seg_001",
            "start_time": 0.0,
            "end_time": 2.0,
            "tags": ["nudity"],
            "risk": "mature",
            "action": "swap",
            "profile_segment_id": "filler_001",
            "media_container": "fmp4",
            "media_payload": b"ftyp....moov....moof....mdat-main",
        },
        {
            "id": "filler_001",
            "start_time": 2.0,
            "end_time": 4.0,
            "tags": [],
            "risk": "safe",
            "action": "play",
            "is_filler": True,
            "media_container": "fmp4",
            "media_payload": b"ftyp....moov....moof....mdat-filler",
        },
    ]
    return BvfMuxer(movie_id="fixture", title="Fixture").write_bvf(
        tmp_path / "fixture.bvf",
        segments=segments,
        duration_seconds=4.0,
        profiles=profiles,
    )


def _rewrite_manifest(path: Path, transform) -> None:
    parsed = BvfMuxer.read_bvf(path)
    manifest = parsed["manifest"]
    transform(manifest)

    raw = path.read_bytes()
    manifest_json = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    manifest_compressed = zstandard.ZstdCompressor(level=3).compress(manifest_json)

    header = parsed["header"]
    manifest_offset = header["manifest_offset"]
    old_manifest_length = header["manifest_length"]
    new_manifest_length = len(manifest_compressed)
    delta = new_manifest_length - old_manifest_length

    updated = bytearray()
    updated.extend(raw[:manifest_offset])
    updated.extend(manifest_compressed)
    updated.extend(raw[manifest_offset + old_manifest_length :])

    struct.pack_into("<Q", updated, 40, new_manifest_length)

    if delta:
        segment_count = header["segment_count"]
        index_offset = header["index_offset"]
        entry_size = 40
        data_offset_pos = 16
        for idx in range(segment_count):
            entry_start = index_offset + idx * entry_size
            current_offset = struct.unpack_from(
                "<Q", updated, entry_start + data_offset_pos
            )[0]
            struct.pack_into(
                "<Q", updated, entry_start + data_offset_pos, current_offset + delta
            )

    path.write_bytes(bytes(updated))


def _run_probe(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "tools/bvf_probe.py", str(path), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _rewrite_index_entry(path: Path, entry_index: int, *, data_offset: int | None = None, data_length: int | None = None) -> None:
    raw = bytearray(path.read_bytes())
    header = BvfMuxer.read_bvf(path)["header"]
    entry_offset = header["index_offset"] + (entry_index * 40)
    if data_offset is not None:
        struct.pack_into("<Q", raw, entry_offset + 16, data_offset)
    if data_length is not None:
        struct.pack_into("<Q", raw, entry_offset + 24, data_length)
    path.write_bytes(bytes(raw))


def _rewrite_header_segment_count(path: Path, segment_count: int) -> None:
    raw = bytearray(path.read_bytes())
    struct.pack_into("<I", raw, 48, segment_count)
    path.write_bytes(bytes(raw))


def _rewrite_index_duration_ms(path: Path, entry_index: int, duration_ms: int) -> None:
    raw = bytearray(path.read_bytes())
    header = BvfMuxer.read_bvf(path)["header"]
    entry_offset = header["index_offset"] + (entry_index * 40)
    struct.pack_into("<Q", raw, entry_offset + 32, duration_ms)
    path.write_bytes(bytes(raw))


def _remux_mp4_payload(source: Path, output: Path) -> bytes:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source),
            "-map",
            "0:v:0?",
            "-map",
            "0:a:0?",
            "-c",
            "copy",
            "-movflags",
            "frag_keyframe+empty_moov+default_base_moof",
            "-f",
            "mp4",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return output.read_bytes()


def _create_audio_only_mp4(path: Path, duration: int, frequency: int) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:duration={duration}",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _write_real_media_bvf(tmp_path: Path, *, container: str = "fmp4") -> Path:
    video = tmp_path / f"probe_{uuid.uuid4().hex}.mp4"
    payload_file = tmp_path / f"probe_{uuid.uuid4().hex}_payload.mp4"
    _create_demo_video(video, duration=2, frequency=440)
    payload = _remux_mp4_payload(video, payload_file)

    profiles = {
        "child": {"name": "Child", "filters": {}},
        "adult": {"name": "Adult", "filters": {}},
    }
    segments = [
        {
            "id": "seg_001",
            "start_time": 0.0,
            "end_time": 2.0,
            "tags": [],
            "risk": "safe",
            "action": "play",
            "media_container": container,
            "media_payload": payload,
        }
    ]
    return BvfMuxer(movie_id="real_media", title="Real Media").write_bvf(
        tmp_path / f"real_media_{uuid.uuid4().hex}.bvf",
        segments=segments,
        duration_seconds=2.0,
        profiles=profiles,
    )


def _write_audio_only_bvf(tmp_path: Path) -> Path:
    audio_mp4 = tmp_path / f"audio_only_{uuid.uuid4().hex}.mp4"
    _create_audio_only_mp4(audio_mp4, duration=2, frequency=660)
    payload = audio_mp4.read_bytes()

    profiles = {
        "child": {"name": "Child", "filters": {}},
        "adult": {"name": "Adult", "filters": {}},
    }
    segments = [
        {
            "id": "seg_001",
            "start_time": 0.0,
            "end_time": 2.0,
            "tags": [],
            "risk": "safe",
            "action": "play",
            "media_container": "fmp4",
            "media_payload": payload,
        }
    ]
    return BvfMuxer(movie_id="audio_only", title="Audio Only").write_bvf(
        tmp_path / f"audio_only_{uuid.uuid4().hex}.bvf",
        segments=segments,
        duration_seconds=2.0,
        profiles=profiles,
    )


def _cut_misaligned_mp4_payload(source: Path, output: Path, *, start: float, duration: float) -> bytes:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(source),
            "-t",
            f"{duration:.3f}",
            "-map",
            "0:v:0?",
            "-map",
            "0:a:0?",
            "-c",
            "copy",
            "-movflags",
            "frag_keyframe+empty_moov+default_base_moof",
            "-f",
            "mp4",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return output.read_bytes()


def _write_misaligned_media_bvf(tmp_path: Path) -> Path:
    video = tmp_path / f"misaligned_{uuid.uuid4().hex}.mp4"
    payload_file = tmp_path / f"misaligned_{uuid.uuid4().hex}_payload.mp4"
    _create_demo_video(video, duration=3, frequency=440)
    payload = _cut_misaligned_mp4_payload(
        video,
        payload_file,
        start=0.5,
        duration=1.5,
    )

    profiles = {
        "child": {"name": "Child", "filters": {}},
        "adult": {"name": "Adult", "filters": {}},
    }
    segments = [
        {
            "id": "seg_001",
            "start_time": 0.0,
            "end_time": 1.5,
            "tags": [],
            "risk": "safe",
            "action": "play",
            "media_container": "fmp4",
            "media_payload": payload,
        }
    ]
    return BvfMuxer(movie_id="misaligned", title="Misaligned").write_bvf(
        tmp_path / f"misaligned_{uuid.uuid4().hex}.bvf",
        segments=segments,
        duration_seconds=1.5,
        profiles=profiles,
    )


def test_probe_script_header_mentions_diagnostics_purpose():
    probe_script = ROOT / "tools" / "bvf_probe.py"

    header = "\n".join(probe_script.read_text().splitlines()[:10]).lower()

    assert "reports bvf structure for diagnostics" in header


def test_probe_accepts_valid_bvf(tmp_path: Path):
    bvf = _write_fixture(tmp_path)

    result = _run_probe(bvf, "--profile", "child")

    assert result.returncode == 0
    assert "OK" in result.stdout
    assert result.stderr == ""


def test_probe_emits_valid_json_payload(tmp_path: Path):
    bvf = _write_fixture(tmp_path)

    result = _run_probe(bvf, "--profile", "child", "--json")

    payload = json.loads(result.stdout)

    assert result.returncode == 0
    assert result.stderr == ""
    assert payload == {
        "issues": [],
        "path": str(bvf),
        "profile": "child",
        "profile_count": 2,
        "segment_count": 2,
        "valid": True,
    }


def test_probe_json_reports_media_validation_summary(tmp_path: Path):
    bvf = _write_fixture(tmp_path)

    result = _run_probe(bvf, "--profile", "child", "--json")

    payload = json.loads(result.stdout)

    assert result.returncode == 0
    assert payload["media_summary"] == {
        "checked_assets": 2,
        "probeable_assets": 2,
        "duration_mismatches": 0,
    }


def test_probe_rejects_swap_without_target(tmp_path: Path):
    bvf = _write_fixture(tmp_path)

    def transform(manifest: dict) -> None:
        manifest["segments"][0]["profiles"]["child"]["segment_id"] = ""

    _rewrite_manifest(bvf, transform)
    result = _run_probe(bvf, "--profile", "child")

    assert result.returncode != 0
    assert "swap" in result.stdout
    assert "target" in result.stdout


def test_probe_emits_invalid_json_payload(tmp_path: Path):
    bvf = _write_fixture(tmp_path)

    def transform(manifest: dict) -> None:
        manifest["segments"][0]["profiles"]["child"]["segment_id"] = ""

    _rewrite_manifest(bvf, transform)
    result = _run_probe(bvf, "--profile", "child", "--json")

    payload = json.loads(result.stdout)

    assert result.returncode != 0
    assert result.stderr == ""
    assert payload == {
        "issues": [
            "Segment seg_001 profile child: swap action requires a non-empty "
            "target segment_id."
        ],
        "path": str(bvf),
        "profile": "child",
        "profile_count": 2,
        "segment_count": 2,
        "valid": False,
    }


def test_probe_rejects_swap_target_that_does_not_exist(tmp_path: Path):
    bvf = _write_fixture(tmp_path)

    def transform(manifest: dict) -> None:
        manifest["segments"][0]["profiles"]["child"]["segment_id"] = "missing_999"

    _rewrite_manifest(bvf, transform)
    result = _run_probe(bvf, "--profile", "child")

    assert result.returncode != 0
    assert "missing_999" in result.stdout
    assert "does not exist" in result.stdout


def test_probe_rejects_unsupported_profile_actions(tmp_path: Path):
    bvf = _write_fixture(tmp_path)

    def transform(manifest: dict) -> None:
        manifest["segments"][0]["profiles"]["child"]["action"] = "explode"

    _rewrite_manifest(bvf, transform)
    result = _run_probe(bvf, "--profile", "child")

    assert result.returncode != 0
    assert "Unsupported action" in result.stdout
    assert "explode" in result.stdout


def test_probe_rejects_index_offsets_outside_the_file(tmp_path: Path):
    bvf = _write_fixture(tmp_path)
    file_size = bvf.stat().st_size
    _rewrite_index_entry(bvf, 0, data_offset=file_size + 4096)

    result = _run_probe(bvf, "--profile", "child")

    assert result.returncode != 0
    assert "data_offset" in result.stdout
    assert "outside the file" in result.stdout


def test_probe_rejects_non_probeable_media_payloads(tmp_path: Path):
    bvf = _write_fixture(tmp_path)

    result = _run_probe(bvf, "--profile", "child")

    assert result.returncode != 0
    assert "ffprobe" in result.stdout
    assert "seg_001" in result.stdout


def test_probe_rejects_header_segment_count_mismatch(tmp_path: Path):
    bvf = _write_fixture(tmp_path)
    _rewrite_header_segment_count(bvf, 3)

    result = _run_probe(bvf, "--profile", "child")

    assert result.returncode != 0
    assert "segment_count" in result.stdout
    assert "header" in result.stdout
    assert "index" in result.stdout


def test_probe_rejects_manifest_segment_count_mismatch(tmp_path: Path):
    bvf = _write_fixture(tmp_path)

    def transform(manifest: dict) -> None:
        manifest["segments"].append(
            {
                "id": "ghost_001",
                "start_ms": 4000,
                "end_ms": 5000,
                "tags": [],
                "risk": "safe",
                "media": {
                    "asset_id": "ghost_001",
                    "container": "fmp4",
                    "mime_type": "video/mp4",
                    "codec_video": 1,
                    "codec_audio": 256,
                },
                "profiles": {
                    "child": {"action": "play", "segment_id": "ghost_001"},
                    "adult": {"action": "play", "segment_id": "ghost_001"},
                },
            }
        )

    _rewrite_manifest(bvf, transform)
    result = _run_probe(bvf, "--profile", "child")

    assert result.returncode != 0
    assert "manifest" in result.stdout
    assert "segments" in result.stdout
    assert "index" in result.stdout


def test_probe_rejects_index_segment_id_missing_from_manifest_media(tmp_path: Path):
    bvf = _write_fixture(tmp_path)

    def transform(manifest: dict) -> None:
        manifest["segments"][0]["media"]["asset_id"] = "renamed_001"

    _rewrite_manifest(bvf, transform)
    result = _run_probe(bvf, "--profile", "child")

    assert result.returncode != 0
    assert "seg_001" in result.stdout
    assert "manifest" in result.stdout
    assert "asset" in result.stdout


def test_probe_rejects_index_data_length_outside_the_file(tmp_path: Path):
    bvf = _write_fixture(tmp_path)
    file_size = bvf.stat().st_size
    parsed = BvfMuxer.read_bvf(bvf)
    entry = parsed["segments"][0]
    _rewrite_index_entry(
        bvf,
        0,
        data_length=(file_size - entry["data_offset"]) + 4096,
    )

    result = _run_probe(bvf, "--profile", "child")

    assert result.returncode != 0
    assert "data_length" in result.stdout
    assert "outside the file" in result.stdout


def test_probe_rejects_asset_block_segment_id_mismatch(tmp_path: Path):
    bvf = _write_fixture(tmp_path)
    parsed = BvfMuxer.read_bvf(bvf)
    first_offset = parsed["segments"][0]["data_offset"]

    raw = bytearray(bvf.read_bytes())
    raw[first_offset + 4 : first_offset + 20] = b"wrong_seg_id\x00\x00\x00\x00"
    bvf.write_bytes(bytes(raw))

    result = _run_probe(bvf, "--profile", "child")

    assert result.returncode != 0
    assert "segment_id" in result.stdout
    assert "asset block" in result.stdout
    assert "seg_001" in result.stdout


def test_probe_accepts_real_media_payload_and_reports_probe_metadata(tmp_path: Path):
    bvf = _write_real_media_bvf(tmp_path)

    result = _run_probe(bvf, "--profile", "child", "--json")
    payload = json.loads(result.stdout)

    assert result.returncode == 0
    assert payload["valid"] is True
    assert payload["media_summary"] == {
        "checked_assets": 1,
        "probeable_assets": 1,
        "duration_mismatches": 0,
    }
    assert payload["media_assets"] == [
        {
            "asset_id": "seg_001",
            "container": "fmp4",
            "has_video": True,
            "has_audio": True,
        }
    ]


def test_probe_rejects_fake_media_payload_with_clear_ffprobe_error(tmp_path: Path):
    bvf = _write_fixture(tmp_path)

    result = _run_probe(bvf, "--profile", "child")

    assert result.returncode != 0
    assert "ffprobe" in result.stdout
    assert "seg_001" in result.stdout


def test_probe_rejects_media_duration_mismatch(tmp_path: Path):
    bvf = _write_real_media_bvf(tmp_path)
    _rewrite_index_duration_ms(bvf, 0, 9000)

    result = _run_probe(bvf, "--profile", "child")

    assert result.returncode != 0
    assert "duration" in result.stdout
    assert "seg_001" in result.stdout
    assert "9000" in result.stdout


def test_probe_rejects_media_without_video_stream(tmp_path: Path):
    bvf = _write_audio_only_bvf(tmp_path)

    result = _run_probe(bvf, "--profile", "child")

    assert result.returncode != 0
    assert "video stream" in result.stdout
    assert "seg_001" in result.stdout


def test_probe_accepts_asset_that_starts_on_a_keyframe(tmp_path: Path):
    bvf = _write_real_media_bvf(tmp_path)

    result = _run_probe(bvf, "--profile", "child", "--json")
    payload = json.loads(result.stdout)

    assert result.returncode == 0
    assert payload["valid"] is True
    assert payload["keyframe_summary"] == {
        "checked_assets": 1,
        "keyframe_aligned_assets": 1,
        "misaligned_assets": 0,
    }


def test_probe_rejects_asset_that_does_not_start_on_a_keyframe(tmp_path: Path):
    bvf = _write_misaligned_media_bvf(tmp_path)

    result = _run_probe(bvf, "--profile", "child")

    assert result.returncode != 0
    assert "keyframe" in result.stdout.lower()
    assert "seg_001" in result.stdout
