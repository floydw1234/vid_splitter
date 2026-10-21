from __future__ import annotations

import importlib
import inspect
import sys
import types
from pathlib import Path
import pytest
from PIL import Image

from analyzer.analyze import MovieAnalyzer
from analyzer.clef_topics import (
    ClefFlashTopicScanner,
    DEFAULT_CLEF_SCAN_INTERVAL,
    build_clef_visual_scanner,
    clef_active_labels,
)


@pytest.fixture
def fake_joint_schema(monkeypatch, tmp_path: Path):
    try:
        import torch
    except ImportError:
        pytest.skip("torch not installed")

    load_calls: list[str] = []

    class FakeQuestion:
        def __init__(self, question_id: str, option_ids: list[str]):
            self.question_id = question_id
            self.option_ids = option_ids

    class FakeEncoded:
        def __init__(self, questions):
            self.questions = questions
            self.input_ids = [1, 2, 3]

    logits_by_qid = {
        "asian_people": torch.tensor([0.0, 2.0]),
        "black_people": torch.tensor([0.0, -0.04]),
        "lgbtq": torch.tensor([1.0, 0.0]),
    }

    def encode_record(tokenizer, rec, processor=None):
        questions = [
            FakeQuestion(qid, ["false", "true"]) for qid in rec["questions"]
        ]
        return FakeEncoded(questions)

    def collate_records(records, pad_token_id, device):
        return records[0]

    class FakeModel:
        def eval(self):
            return self

        def __call__(self, batch):
            per_question = [logits_by_qid[q.question_id] for q in batch.questions]
            return [per_question]

    fake_model = FakeModel()
    fake_processor = types.SimpleNamespace(
        tokenizer=types.SimpleNamespace(pad_token_id=0),
    )

    def load_release_model(path, device="cuda", dtype=None):
        load_calls.append(str(path))
        return fake_model, fake_processor

    snap = tmp_path / "clef_snap"
    snap.mkdir()
    (snap / "joint_schema_model.py").write_text("# fake\n", encoding="utf-8")

    module = types.ModuleType("joint_schema_model")
    module.collate_records = collate_records
    module.encode_record = encode_record
    module.load_release_model = load_release_model
    monkeypatch.setitem(sys.modules, "joint_schema_model", module)

    monkeypatch.setattr(
        "analyzer.clef_topics.resolve_clef_snapshot_dir",
        lambda model_path: str(snap),
    )

    return {"load_calls": load_calls, "snap": snap}


def test_clef_threshold_mapping(fake_joint_schema):
    image = Image.new("RGB", (4, 4), color="white")
    scanner = ClefFlashTopicScanner(
        model_path=str(fake_joint_schema["snap"]), threshold=0.5, device="cpu"
    )
    topics = scanner.topics_for_image(image)
    assert topics == ["asian people"]
    scores = scanner.last_scores
    assert scores["black people"] >= 0.49
    assert scores["black people"] < 0.5

    scanner.threshold = 0.49
    low_topics = scanner.topics_for_image(image)
    assert "black people" in low_topics
    assert len(fake_joint_schema["load_calls"]) == 1


def test_clef_label_mapping(fake_joint_schema):
    image = Image.new("RGB", (4, 4), color="white")
    scores = ClefFlashTopicScanner(
        model_path=str(fake_joint_schema["snap"]), threshold=0.0, device="cpu"
    ).scores_for_image(image)
    assert set(scores) == {"asian people", "black people", "lgbtq"}


def test_clef_lazy_single_load(fake_joint_schema):
    scanner = ClefFlashTopicScanner(
        model_path=str(fake_joint_schema["snap"]), device="cpu"
    )
    image = Image.new("RGB", (2, 2))
    assert fake_joint_schema["load_calls"] == []
    scanner.topics_for_image(image)
    path = fake_joint_schema["snap"] / "frame.png"
    Image.new("RGB", (2, 2)).save(path)
    scanner.topics_for_path(path)
    assert len(fake_joint_schema["load_calls"]) == 1


def test_import_clef_topics_does_not_import_torch():
    saved = {
        name: sys.modules[name]
        for name in list(sys.modules)
        if name == "torch" or name.startswith("torch.")
    }
    for name in saved:
        del sys.modules[name]
    if "analyzer.clef_topics" in sys.modules:
        del sys.modules["analyzer.clef_topics"]
    module = importlib.import_module("analyzer.clef_topics")
    assert "torch" not in sys.modules
    assert module.ClefFlashTopicScanner is not None
    sys.modules.update(saved)


def test_clef_active_labels_default_and_filters():
    assert clef_active_labels() == frozenset({"lgbtq"})
    assert clef_active_labels(("nudity",)) == frozenset({"lgbtq"})
    mixed = clef_active_labels(("Black people", "Asian Person", "LGBTQ", "nudity"))
    assert mixed == frozenset({"Black people", "Asian Person", "LGBTQ", "lgbtq"})


def test_clef_returns_original_taxonomy_keys(fake_joint_schema):
    image = Image.new("RGB", (4, 4), color="white")
    scanner = ClefFlashTopicScanner(
        model_path=str(fake_joint_schema["snap"]),
        threshold=0.5,
        device="cpu",
        active_labels={"asian people", "Asian Person", "black people"},
    )
    topics = scanner.topics_for_image(image)
    assert topics == ["Asian Person", "asian people"]
    assert "black people" not in topics
    scores = scanner.last_scores
    assert scores["asian people"] == scores["Asian Person"]


def test_clef_only_runs_requested_questions(fake_joint_schema, monkeypatch):
    encoded_qids: list[str] = []
    orig_encode = sys.modules["joint_schema_model"].encode_record

    def capture_encode(tokenizer, rec, processor=None):
        encoded_qids.extend(rec["questions"].keys())
        return orig_encode(tokenizer, rec, processor=processor)

    monkeypatch.setattr(
        sys.modules["joint_schema_model"],
        "encode_record",
        capture_encode,
    )
    image = Image.new("RGB", (4, 4), color="white")
    ClefFlashTopicScanner(
        model_path=str(fake_joint_schema["snap"]),
        device="cpu",
        active_labels={"LGBTQ"},
    ).scores_for_image(image)
    assert encoded_qids == ["lgbtq"]


def test_nudity_only_filter_keeps_default_lgbtq_clef_labels(fake_joint_schema):
    scanner = build_clef_visual_scanner(
        filter_topics=("nudity",),
        device="cpu",
        clef_model_path=str(fake_joint_schema["snap"]),
        clef_threshold=0.5,
    )
    assert scanner.active_labels == frozenset({"lgbtq"})
    assert "nudity" not in scanner.active_labels


def test_build_clef_visual_scanner_logs_active(caplog, fake_joint_schema):
    with caplog.at_level("INFO"):
        build_clef_visual_scanner(
            filter_topics=("asian people",),
            device="cpu",
            clef_model_path=str(fake_joint_schema["snap"]),
            clef_threshold=0.5,
        )
    assert "Visual topics (clef-flash): [asian people, lgbtq]" in caplog.text


def test_analyzer_defaults_clef_interval(tmp_path: Path):
    analyzer = MovieAnalyzer(str(tmp_path / "movie.mp4"), load_models=False)
    assert analyzer.clef_scan_interval == DEFAULT_CLEF_SCAN_INTERVAL
    assert analyzer._effective_visual_scan_interval() == DEFAULT_CLEF_SCAN_INTERVAL


def test_analyze_cli_clef_scan_interval_default():
    import analyzer.analyze as analyze_mod

    source = inspect.getsource(analyze_mod.main)
    assert "DEFAULT_CLEF_SCAN_INTERVAL" in source
    assert "--visual-backend" not in source


def test_apply_visual_topics_injected_scanner_unchanged(tmp_path: Path):
    frame = tmp_path / "frame.jpg"
    Image.new("RGB", (8, 8), color="white").save(frame)

    class Scanner:
        def topics_for_path(self, path):
            return ["religion_christianity"]

    analyzer = MovieAnalyzer(
        str(tmp_path / "movie.mp4"),
        load_models=False,
        visual_topic_scanner=Scanner(),
        classify_topics=False,
    )
    analyzer._scanned_frames = [{"time": 2.0, "frame_path": str(frame)}]
    result = analyzer._apply_visual_topics(
        [{"id": "seg_001", "start_time": 0.0, "end_time": 5.0, "topics": []}]
    )
    assert result[0]["topics"] == ["religion_christianity"]


def test_clef_load_failure_skips_visual_topics(tmp_path: Path, monkeypatch, caplog):
    frame = tmp_path / "frame.jpg"
    Image.new("RGB", (8, 8), color="white").save(frame)

    analyzer = MovieAnalyzer(
        str(tmp_path / "movie.mp4"),
        load_models=False,
        classify_topics=False,
        filter_topics=("lgbtq",),
    )
    analyzer._scanned_frames = [{"time": 2.0, "frame_path": str(frame)}]

    def fail_build(**kwargs):
        raise OSError("clef weights missing")

    monkeypatch.setattr(
        "analyzer.clef_topics.build_clef_visual_scanner",
        fail_build,
    )

    with caplog.at_level("WARNING"):
        result = analyzer._apply_visual_topics(
            [{"id": "seg_001", "start_time": 0.0, "end_time": 5.0, "topics": []}]
        )
    assert "Visual topic scanner unavailable" in caplog.text
    assert result[0]["topics"] == []
