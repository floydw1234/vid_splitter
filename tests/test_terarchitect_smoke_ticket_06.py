from pathlib import Path


def test_terarchitect_smoke_ticket_06_doc_exists_with_exact_required_content():
    repo_root = Path(__file__).resolve().parents[1]
    doc_path = repo_root / "docs" / "terarchitect-smoke" / "ticket-06.md"

    assert doc_path.exists(), f"Expected smoke ticket doc at {doc_path}"

    contents = doc_path.read_text(encoding="utf-8")
    lines = contents.splitlines()

    assert lines[0] == "# Terarchitect Smoke Ticket 06"
    assert "Ticket: 06" in lines
    assert (
        "Purpose: verify competing attempts, winner selection, acceptance, and "
        "Ship Room composition on an isolated file."
    ) in lines
    assert any(
        "intentionally independent" in line.lower() and "other" in line.lower()
        for line in lines
    )
