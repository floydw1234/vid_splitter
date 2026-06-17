from pathlib import Path


def test_terarchitect_smoke_ticket_08_marker_file_contains_required_lines():
    repo_root = Path(__file__).resolve().parents[1]
    marker_path = repo_root / "docs" / "terarchitect-smoke" / "ticket-08.md"

    assert marker_path.exists()

    contents = marker_path.read_text(encoding="utf-8")
    lines = contents.splitlines()

    assert lines[0] == "# Terarchitect Smoke Ticket 08"
    assert "Ticket: 08" in lines
    assert (
        "Purpose: verify competing attempts, winner selection, acceptance, and Ship Room composition on an isolated file."
        in lines
    )
    assert "intentionally independent of the other nine smoke tickets" in contents
