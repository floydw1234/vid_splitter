import builtins
import sys

from analyzer.analyze import MovieAnalyzer


def test_classify_topics_defaults_to_empty_when_httpx_missing(monkeypatch, tmp_path):
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
