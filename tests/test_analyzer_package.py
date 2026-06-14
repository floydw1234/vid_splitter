from pathlib import Path

import analyzer


def test_analyzer_package_comment_marks_smoke_ticket_without_docstring():
    package_init = Path(__file__).resolve().parents[1] / "analyzer" / "__init__.py"
    content = package_init.read_text()

    assert analyzer.__doc__ is None
    assert "concurrency smoke ticket 04" in content


def test_skin_detector_module_contains_heuristic_and_conservative_comment():
    skin_detector = Path(__file__).resolve().parents[1] / "analyzer" / "skin_detector.py"
    content = skin_detector.read_text()

    assert "heuristic and conservative" in content
