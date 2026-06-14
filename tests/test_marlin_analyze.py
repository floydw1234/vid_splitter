from pathlib import Path


def test_marlin_analyze_top_section_has_optional_model_backed_comment():
    target = Path(__file__).resolve().parents[1] / "analyzer" / "marlin_analyze.py"
    top_text = "\n".join(target.read_text(encoding="utf-8").splitlines()[:20]).lower()

    assert "marlin analysis is an optional model-backed path" in top_text
