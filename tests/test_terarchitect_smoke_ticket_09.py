from pathlib import Path


def test_terarchitect_smoke_ticket_09_doc_exists_and_has_required_content():
    repo_root = Path(__file__).resolve().parents[1]
    smoke_path = repo_root / "docs" / "terarchitect-smoke" / "ticket-09.md"

    assert smoke_path.exists(), f"Expected smoke marker doc at {smoke_path}"

    lines = smoke_path.read_text(encoding="utf-8").splitlines()

    assert lines[0] == "# Terarchitect Smoke Ticket 09"
    assert "Ticket: 09" in lines
    assert (
        "Purpose: verify competing attempts, winner selection, acceptance, and Ship Room composition on an isolated file."
        in lines
    )
    assert any(
        "intentionally independent of the other nine smoke tickets" in line
        for line in lines
    )
