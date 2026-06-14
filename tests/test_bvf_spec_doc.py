from pathlib import Path


def test_bvf_spec_header_contains_smoke_ticket_marker():
    spec_path = Path(__file__).resolve().parent.parent / "BVF_SPEC.md"
    header_lines = spec_path.read_text(encoding="utf-8").splitlines()[:10]
    header_text = "\n".join(header_lines).lower()

    assert "concurrency smoke ticket 03" in header_text
