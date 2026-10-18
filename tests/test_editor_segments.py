from editor.segments import merge_flagged_frames


def test_merge_consecutive_frames_same_topic():
    frames = [
        {"t": 0.0, "scores": {"asian people": 0.8}},
        {"t": 2.0, "scores": {"asian people": 0.9}},
    ]
    out = merge_flagged_frames(frames, interval=2.0, threshold=0.5, pad=0.5, duration=10.0)
    assert len(out) == 1
    assert out[0].topic == "asian people"
    assert out[0].start == 0.0
    assert out[0].end == 2.5
    assert out[0].score == 0.9


def test_gap_splits_segments():
    frames = [
        {"t": 0.0, "scores": {"lgbtq": 0.7}},
        {"t": 10.0, "scores": {"lgbtq": 0.75}},
    ]
    out = merge_flagged_frames(frames, interval=2.0, threshold=0.5, pad=0.0, duration=20.0)
    assert len(out) == 2


def test_below_threshold_ignored():
    frames = [{"t": 1.0, "scores": {"black people": 0.2}}]
    out = merge_flagged_frames(frames, interval=2.0, threshold=0.5, duration=5.0)
    assert out == []


def test_per_topic_separation():
    frames = [{"t": 1.0, "scores": {"asian people": 0.9, "lgbtq": 0.8}}]
    out = merge_flagged_frames(frames, interval=2.0, threshold=0.5, pad=0.0, duration=5.0)
    topics = {s.topic for s in out}
    assert topics == {"asian people", "lgbtq"}


def test_clamp_to_duration():
    frames = [{"t": 9.0, "scores": {"asian people": 0.9}}]
    out = merge_flagged_frames(frames, interval=2.0, threshold=0.5, pad=1.0, duration=10.0)
    assert out[0].end == 10.0
