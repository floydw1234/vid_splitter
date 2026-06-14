from pathlib import Path

import vid_splitter


def test_vid_splitter_package_has_no_docstring():
    assert vid_splitter.__doc__ is None


def test_vid_splitter_package_init_contains_smoke_05_comment():
    init_path = Path(__file__).resolve().parents[1] / "vid_splitter" / "__init__.py"
    init_text = init_path.read_text(encoding="utf-8")

    assert "concurrency smoke ticket 05" in init_text.lower()
