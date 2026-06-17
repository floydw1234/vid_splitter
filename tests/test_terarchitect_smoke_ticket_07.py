from pathlib import Path


def test_terarchitect_smoke_ticket_07_marker_file_has_exact_required_lines():
    repo_root = Path(__file__).resolve().parents[1]
    marker_path = repo_root / "docs" / "terarchitect-smoke" / "ticket-07.md"

    assert marker_path.is_file(), f"Missing smoke marker file: {marker_path}"

    lines = marker_path.read_text(encoding="utf-8").splitlines()

    assert lines[0] == "# Terarchitect Smoke Ticket 07"
    assert "Ticket: 07" in lines
    assert (
        "Purpose: verify competing attempts, winner selection, acceptance, and Ship Room composition on an isolated file."
        in lines
    )

    lowered = marker_path.read_text(encoding="utf-8").lower()
    assert "intentionally independent" in lowered
    assert "other nine smoke tickets" in lowered
