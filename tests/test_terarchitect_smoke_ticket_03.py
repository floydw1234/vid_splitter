from pathlib import Path


def test_terarchitect_smoke_ticket_03_doc_exists_and_contains_required_lines():
    repo_root = Path(__file__).resolve().parents[1]
    smoke_doc_path = repo_root / "docs" / "terarchitect-smoke" / "ticket-03.md"

    assert smoke_doc_path.exists(), "Expected docs/terarchitect-smoke/ticket-03.md to exist."

    lines = smoke_doc_path.read_text(encoding="utf-8").splitlines()
    non_empty_lines = [line for line in lines if line.strip()]

    assert non_empty_lines[0] == "# Terarchitect Smoke Ticket 03"
    assert "Ticket: 03" in lines
    assert (
        "Purpose: verify competing attempts, winner selection, acceptance, and Ship Room composition on an isolated file."
        in lines
    )

    lowered = smoke_doc_path.read_text(encoding="utf-8").lower()
    assert "intentionally independent of the other nine smoke tickets" in lowered
