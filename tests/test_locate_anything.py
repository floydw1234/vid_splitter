from analyzer.locate_anything import (
    CombinedVisualScanner,
    LocateAnythingScanner,
    labels_to_topics,
    locate_prompt,
    merge_visual_labels,
)


def test_locate_prompt_joins_categories():
    prompt = locate_prompt({"black people": "Black person", "lgbtq": "rainbow pride flag"})
    assert "Black person</c>rainbow pride flag" in prompt
    assert prompt.startswith("Locate all the instances")


def test_labels_to_topics_matches_prompt_text():
    dets = [
        {"label": "Black person", "box": [1, 2, 3, 4]},
        {"label": "car", "box": [0, 0, 1, 1]},
    ]
    assert labels_to_topics(dets, {"black people": "Black person"}) == ["black people"]


def test_lesbians_maps_kiss_and_butch_style():
    prompts = {"lesbians": ["two women kissing", "butch lesbian woman"]}
    assert labels_to_topics(
        [{"label": "butch lesbian woman", "box": [0, 0, 1, 1]}],
        prompts,
    ) == ["lesbians"]
    assert "butch lesbian woman" in locate_prompt(prompts)


def test_merge_drops_clip_false_positive_and_keeps_locate_miss():
    merged = merge_visual_labels(
        ["black people", "lgbtq", "nudity"],
        ["black people"],
        locate_keys={"black people", "asian people", "lgbtq"},
    )
    assert merged == ["black people", "nudity"]


def test_scanner_uses_injected_locate(tmp_path):
    image = tmp_path / "frame.jpg"
    image.write_bytes(b"not-an-image")
    seen = []

    def fake_locate(path, prompt, mode):
        seen.append((path, prompt, mode))
        return [{"label": "East Asian person", "box": [0, 0, 10, 10]}]

    scanner = LocateAnythingScanner(locate=fake_locate)
    assert scanner.topics_for_path(image) == ["asian people"]
    assert seen and "East Asian person" in seen[0][1]


def test_combined_scanner_lets_locate_own_appearance_keys():
    class Clip:
        def topics_for_path(self, path):
            return ["black people", "lgbtq", "religion_christianity"]

    calls = {"per_topic": False}

    def fake_locate(path, prompt, mode):
        if "Black person" in prompt:
            return [{"label": "Black person", "box": [0, 0, 1, 1]}]
        return []

    locate = LocateAnythingScanner(locate=fake_locate)
    original = locate.topics_for_path

    def wrapped(path, *, per_topic=False):
        calls["per_topic"] = per_topic
        return original(path, per_topic=per_topic)

    locate.topics_for_path = wrapped  # type: ignore[method-assign]
    combined = CombinedVisualScanner(Clip(), locate)
    assert combined.topics_for_path("frame.jpg") == ["black people"]
    assert calls["per_topic"] is True
