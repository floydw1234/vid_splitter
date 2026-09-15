from analyzer.wan_replace import (
    chunk_span,
    coalesce_tagged_runs,
    merge_run_segments,
    parse_csv,
    prompt_for_tags,
)


def _seg(seg_id, start, end, tags, topics=None):
    return {
        "id": seg_id,
        "start_time": start,
        "end_time": end,
        "tags": tags,
        "topics": list(topics or []),
        "risk": "mature" if tags else "safe",
        "action": "swap" if tags else "play",
    }


def test_parse_csv_strips_and_drops_empties():
    assert parse_csv("nudity, gore ,") == ("nudity", "gore")
    assert parse_csv("") == ()
    assert parse_csv(None) == ()


def test_coalesce_joins_adjacent_matching_buckets_only():
    segments = [
        _seg("seg_001", 0, 5, []),
        _seg("seg_002", 5, 10, ["nudity"]),
        _seg("seg_003", 10, 15, ["nudity"]),
        _seg("seg_004", 15, 20, ["gore"]),
        _seg("seg_005", 25, 30, ["nudity"]),
    ]
    runs = coalesce_tagged_runs(segments, tags=("nudity",))
    assert [(r.start, r.end, r.segment_ids) for r in runs] == [
        (5, 15, ("seg_002", "seg_003")),
        (25, 30, ("seg_005",)),
    ]


def test_coalesce_respects_segment_id_filter():
    segments = [
        _seg("seg_002", 5, 10, ["nudity"]),
        _seg("seg_003", 10, 15, ["nudity"]),
        _seg("seg_004", 15, 20, ["nudity"]),
    ]
    runs = coalesce_tagged_runs(
        segments,
        tags=("nudity",),
        segment_ids=("seg_003",),
    )
    assert len(runs) == 1
    assert runs[0].segment_ids == ("seg_003",)
    assert runs[0].start == 10
    assert runs[0].end == 15


def test_chunk_span_splits_long_runs_with_overlap():
    assert chunk_span(0, 10, 12, 0.5) == [(0.0, 10.0)]
    assert chunk_span(5, 25, 12, 0.5) == [(5.0, 17.0), (16.5, 25.0)]


def test_merge_run_segments_keeps_first_id_and_drops_the_rest():
    segments = [
        _seg("seg_001", 0, 5, []),
        _seg("seg_002", 5, 10, ["nudity"]),
        _seg("seg_003", 10, 15, ["nudity"]),
        _seg("seg_004", 15, 20, []),
    ]
    runs = coalesce_tagged_runs(segments, tags=("nudity",))
    merged = merge_run_segments(segments, runs)
    ids = [seg["id"] for seg in merged]
    assert ids == ["seg_001", "seg_002", "seg_004"]
    mature = next(seg for seg in merged if seg["id"] == "seg_002")
    assert mature["start_time"] == 5
    assert mature["end_time"] == 15
    assert mature["action"] == "swap"


def test_coalesce_matches_topics_as_well_as_tags():
    segments = [
        _seg("seg_001", 0, 5, []),
        _seg("seg_002", 5, 10, ["nudity"], topics=["religion_christianity"]),
        _seg("seg_003", 10, 15, [], topics=["religion_christianity"]),
        _seg("seg_004", 15, 20, []),
    ]
    runs = coalesce_tagged_runs(
        segments,
        tags=("nudity", "religion_christianity"),
    )
    assert len(runs) == 1
    assert runs[0].segment_ids == ("seg_002", "seg_003")
    assert set(runs[0].tags) == {"nudity", "religion_christianity"}


def test_prompt_for_tags_lists_every_label_and_appends_extra():
    prompt = prompt_for_tags(("nudity", "religion_christianity"), "keep the dog")
    assert "modest clothing" in prompt
    assert "Christian" in prompt
    assert prompt.endswith("keep the dog")
    assert "Remove or replace:" in prompt
