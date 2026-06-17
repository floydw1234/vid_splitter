from pathlib import Path


def test_ticket_02_smoke_doc_exists_and_contains_required_marker_content():
    repo_root = Path(__file__).resolve().parents[1]
    doc_path = repo_root / "docs" / "terarchitect-smoke" / "ticket-02.md"

    assert doc_path.exists()

    contents = doc_path.read_text(encoding="utf-8")

    assert "# Terarchitect Smoke Ticket 02" in contents
    assert "Ticket: 02" in contents
    assert (
        "Purpose: verify competing attempts, winner selection, acceptance, "
        "and Ship Room composition on an isolated file."
    ) in contents
    assert "independent" in contents.lower()
    assert "other nine smoke tickets" in contents.lower()
