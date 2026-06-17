from pathlib import Path


def test_terarchitect_smoke_ticket_10_doc_contains_required_marker_lines():
    repo_root = Path(__file__).resolve().parents[1]
    doc_path = repo_root / "docs" / "terarchitect-smoke" / "ticket-10.md"

    assert doc_path.exists()

    lines = doc_path.read_text(encoding="utf-8").splitlines()

    assert lines[0] == "# Terarchitect Smoke Ticket 10"
    assert "Ticket: 10" in lines
    assert (
        "Purpose: verify competing attempts, winner selection, acceptance, and "
        "Ship Room composition on an isolated file."
    ) in lines
    assert (
        "This file is intentionally independent of the other nine smoke tickets."
        in lines
    )
