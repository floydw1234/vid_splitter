from pathlib import Path

import analyzer


def test_analyzer_package_comment_marks_smoke_ticket_without_docstring():
    package_init = Path(__file__).resolve().parents[1] / "analyzer" / "__init__.py"
    content = package_init.read_text()

    assert analyzer.__doc__ is None
    assert "lightweight video analysis helpers" in content
    assert "concurrency smoke ticket 04" in content


def test_analyze_module_keeps_docstring_and_has_coordinator_comment():
    analyze_path = Path(__file__).resolve().parents[1] / "analyzer" / "analyze.py"
    content = analyze_path.read_text(encoding="utf-8")

    assert content.startswith('"""')
    assert "Smart Branching Analyzer" in content
    assert "coordinates analysis" in content


def test_skin_detector_module_contains_heuristic_and_conservative_comment():
    skin_detector = Path(__file__).resolve().parents[1] / "analyzer" / "skin_detector.py"
    content = skin_detector.read_text()

    assert "heuristic and conservative" in content
