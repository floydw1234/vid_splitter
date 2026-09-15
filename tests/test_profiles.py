from pathlib import Path

from analyzer.profiles import load_profile_file, load_profile_files


def test_load_profile_file_uses_stem_and_filters():
    path = Path(__file__).resolve().parents[1] / "examples" / "strict_parent.json"
    loaded = load_profile_file(path)
    assert "strict_parent" in loaded
    assert loaded["strict_parent"]["filters"]["religion_christianity"] == "skip"


def test_load_profile_files_merges_mapping(tmp_path: Path):
    path = tmp_path / "custom.json"
    path.write_text(
        '{"profiles": {"church_skip": {"name": "Church skip", "filters": {"religion_christianity": "skip"}}}}'
    )
    loaded = load_profile_files([path])
    assert loaded["church_skip"]["filters"]["religion_christianity"] == "skip"
