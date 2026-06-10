import builtins
import sys
import types

from analyzer.topic_classifier import LLMTopicClassifier


def test_classify_topics_defaults_to_empty_when_httpx_missing(monkeypatch, tmp_path):
    numpy_stub = types.ModuleType("numpy")
    numpy_stub.ndarray = object
    monkeypatch.setitem(sys.modules, "numpy", numpy_stub)
    monkeypatch.setitem(sys.modules, "zstandard", types.ModuleType("zstandard"))
    sys.modules.pop("analyzer.analyze", None)
    from analyzer.analyze import MovieAnalyzer

    analyzer = MovieAnalyzer(str(tmp_path / "movie.mp4"), load_models=False)
    analyzer._transcript_data = {"segments": []}
    segments = [
        {"id": "seg_001", "start_time": 0.0, "end_time": 5.0, "tags": []},
        {"id": "seg_002", "start_time": 5.0, "end_time": 10.0, "tags": []},
    ]

    original_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "httpx":
            raise ModuleNotFoundError("No module named 'httpx'")
        return original_import(name, globals, locals, fromlist, level)

    sys.modules.pop("analyzer.topic_classifier", None)
    monkeypatch.setattr(builtins, "__import__", fake_import)

    classified = analyzer._classify_topics(segments)

    assert classified == [
        {"id": "seg_001", "start_time": 0.0, "end_time": 5.0, "tags": [], "topics": []},
        {"id": "seg_002", "start_time": 5.0, "end_time": 10.0, "tags": [], "topics": []},
    ]


def make_classifier(topics=None):
    classifier = object.__new__(LLMTopicClassifier)
    classifier.topics = topics or {
        "politics": "government and elections",
        "religion_general": "religious themes",
        "sports": "sports discussion",
    }
    return classifier


def test_parse_topics_accepts_plain_json_array():
    classifier = make_classifier()

    parsed = classifier._parse_topics('["politics", "sports"]')

    assert parsed == ["politics", "sports"]


def test_parse_topics_extracts_json_from_markdown_fence():
    classifier = make_classifier()

    parsed = classifier._parse_topics('```json\n["politics"]\n```')

    assert parsed == ["politics"]


def test_parse_topics_extracts_json_when_explanatory_text_wraps_fenced_block():
    classifier = make_classifier()
    content = 'Here are the matching topics:\n```json\n["religion_general"]\n```\nUse them as needed.'

    parsed = classifier._parse_topics(content)

    assert parsed == ["religion_general"]


def test_parse_topics_extracts_inline_json_array_from_explanatory_text():
    classifier = make_classifier()

    parsed = classifier._parse_topics('Topics present: ["sports"]')

    assert parsed == ["sports"]


def test_parse_topics_filters_unknown_topics_from_extracted_json():
    classifier = make_classifier()

    parsed = classifier._parse_topics('["politics", "unknown_label"]')

    assert parsed == ["politics"]


def test_parse_topics_returns_empty_for_non_json_non_matching_text():
    classifier = make_classifier()

    parsed = classifier._parse_topics("No matching topics found.")

    assert parsed == []


def test_parse_topics_prefers_fenced_json_over_bracketed_explanatory_text():
    classifier = make_classifier()
    content = (
        "The model considered [politics] during reasoning.\n"
        "```json\n"
        '["sports"]\n'
        "```"
    )

    parsed = classifier._parse_topics(content)

    assert parsed == ["sports"]


def test_parse_topics_prefers_uppercase_json_fence_over_earlier_inline_array():
    classifier = make_classifier()
    content = (
        'Candidate labels: ["politics"]\n'
        "```JSON\n"
        '["sports"]\n'
        "```"
    )

    parsed = classifier._parse_topics(content)

    assert parsed == ["sports"]
