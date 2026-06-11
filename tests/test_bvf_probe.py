import json
import struct
import subprocess
import sys
from pathlib import Path

import zstandard

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
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )


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
