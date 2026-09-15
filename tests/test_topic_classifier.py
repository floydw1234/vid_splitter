import builtins
import sys
import types

from analyzer.topic_classifier import (
    DEFAULT_EMBED_MARGIN,
    EmbedTopicClassifier,
    LLMTopicClassifier,
    TOPIC_TAXONOMY,
    cosine_similarity,
    make_topic_classifier,
    taxonomy_with_extras,
)


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


def test_parse_batch_topics_accepts_array_of_arrays():
    classifier = make_classifier()
    parsed = classifier._parse_batch_topics(
        '[["politics"], [], ["religion_general"]]',
        3,
    )
    assert parsed == [["politics"], [], ["religion_general"]]


def test_taxonomy_with_extras_adds_custom_labels():
    taxonomy = taxonomy_with_extras(("asian people", "politics"), TOPIC_TAXONOMY)
    assert taxonomy["asian people"] == 'content matching "asian people"'
    assert taxonomy["politics"] == TOPIC_TAXONOMY["politics"]


def test_transcript_topic_taxonomy_drops_appearance_labels():
    from analyzer.topic_classifier import transcript_topic_taxonomy

    taxonomy = transcript_topic_taxonomy(("asian people", "black people", "lgbtq"))
    assert "asian people" not in taxonomy
    assert "black people" not in taxonomy
    assert "lgbtq" in taxonomy


def test_filter_misleading_transcript_topics_drops_black_on_color_phrase():
    from analyzer.topic_classifier import filter_misleading_transcript_topics

    text = "it's a black and white dockie so cute woof woof"
    topics = ["black people", "lgbtq"]
    assert filter_misleading_transcript_topics(text, topics) == []
    assert filter_misleading_transcript_topics("hello kids", topics) == topics


def test_coerce_topic_list_matches_custom_labels_case_insensitively():
    classifier = make_classifier({"asian people": 'content matching "asian people"'})
    parsed = classifier._parse_topics('["Asian People"]')
    assert parsed == ["asian people"]


def test_parse_batch_topics_accepts_index_object():
    classifier = make_classifier()
    parsed = classifier._parse_batch_topics(
        '{"0": ["sports"], "1": []}',
        2,
    )
    assert parsed == [["sports"], []]


class FakeEmbedClient:
    def __init__(self, table: dict[str, list[float]]):
        self.table = table
        self.calls: list[list[str]] = []

    def post(self, url, json):
        assert url.endswith("/embed")
        texts = json["texts"]
        self.calls.append(texts)
        embeddings = []
        for text in texts:
            if text not in self.table:
                raise AssertionError(f"unexpected embed text: {text!r}")
            embeddings.append(self.table[text])
        return FakeEmbedResponse(embeddings)


class FakeEmbedResponse:
    def __init__(self, embeddings):
        self._payload = {
            "embeddings": embeddings,
            "model_id": "minilm-fast",
            "model_name": "all-MiniLM-L6-v2",
            "dimension": len(embeddings[0]) if embeddings else 0,
        }

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _embed_clf(table, topics=None, margin=DEFAULT_EMBED_MARGIN):
    return EmbedTopicClassifier(
        topics=topics or {
            "politics": "government and elections",
            "religion_christianity": "Christian themes, Jesus",
        },
        margin=margin,
        client=FakeEmbedClient(table),
    )


def test_cosine_similarity_dot_product():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_embed_classifier_keeps_topics_that_beat_none():
    from analyzer.topic_classifier import NONE_EMBED_PROMPT

    politics_p = "A transcript about politics: government and elections"
    religion_p = "A transcript about religion_christianity: Christian themes, Jesus"
    table = {
        politics_p: [1.0, 0.0],
        religion_p: [0.0, 1.0],
        NONE_EMBED_PROMPT: [0.707, 0.707],
        "the president signed a new election policy": [1.0, 0.0],
        "hello kids clap your hands": [0.707, 0.707],
    }
    clf = _embed_clf(table)
    labeled = clf.classify_segments(
        [
            {"transcript": "the president signed a new election policy", "start_time": 0, "end_time": 5},
            {"transcript": "hello kids clap your hands", "start_time": 5, "end_time": 10},
        ]
    )
    assert labeled[0]["topics"] == ["politics"]
    assert labeled[1]["topics"] == []


def test_embed_classifier_keyword_profanity():
    from analyzer.topic_classifier import NONE_EMBED_PROMPT

    politics_p = "A transcript about politics: government and elections"
    religion_p = "A transcript about religion_christianity: Christian themes, Jesus"
    table = {
        politics_p: [1.0, 0.0],
        religion_p: [0.0, 1.0],
        NONE_EMBED_PROMPT: [1.0, 0.0],
        "what the fuck is going on": [0.2, 0.2],
    }
    clf = EmbedTopicClassifier(
        topics={
            "politics": "government and elections",
            "religion_christianity": "Christian themes, Jesus",
            "profanity": "swearing",
        },
        client=FakeEmbedClient(
            {
                **table,
                "A transcript about profanity: swearing": [0.0, 0.5],
            }
        ),
    )
    labeled = clf.classify_segments(
        [{"transcript": "what the fuck is going on", "start_time": 0, "end_time": 5}]
    )
    assert "profanity" in labeled[0]["topics"]


def test_make_topic_classifier_defaults_to_embed(monkeypatch):
    monkeypatch.delenv("TOPIC_BACKEND", raising=False)
    clf = make_topic_classifier(topics={"politics": "gov"})
    assert isinstance(clf, EmbedTopicClassifier)


def test_make_topic_classifier_llm_opt_in(monkeypatch):
    monkeypatch.setenv("TOPIC_BACKEND", "llm")
    clf = make_topic_classifier(topics={"politics": "gov"})
    assert isinstance(clf, LLMTopicClassifier)
    assert clf.model == "latest"
