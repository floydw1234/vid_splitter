import json
import struct
import subprocess
import sys
from pathlib import Path

import zstandard

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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
