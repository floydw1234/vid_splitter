from pathlib import Path

from analyzer.analyze import MovieAnalyzer


def _build_analyzer(tmp_path: Path) -> MovieAnalyzer:
    return MovieAnalyzer(str(tmp_path / "movie.mp4"), load_models=False)


def test_merge_segments_marks_each_overlapping_bucket(tmp_path: Path):
    analyzer = _build_analyzer(tmp_path)

    segments = analyzer._merge_segments(
        [
            {
                "time": 6.0,
                "type": "nudity",
                "score": 0.9,
                "bad_start": 6.0,
                "bad_end": 18.0,
            }
        ],
        duration=22.0,
    )

    assert [(seg["start_time"], seg["end_time"], seg["risk"]) for seg in segments] == [
        (0, 5, "safe"),
        (5, 10, "mature"),
        (10, 15, "mature"),
        (15, 20, "mature"),
        (20, 22.0, "safe"),
    ]
    assert all(seg["tags"] == ["nudity"] for seg in segments[1:4])


def test_attach_goldylocks_fillers_adds_deterministic_swap_targets(tmp_path: Path, monkeypatch):
    analyzer = _build_analyzer(tmp_path)
    filler_video = tmp_path / "goldylocks.mp4"
    filler_video.write_bytes(b"not-a-real-video")
    analyzer.goldylocks_filler_video = filler_video

    monkeypatch.setattr(analyzer, "_get_duration_for_path", lambda path: 30.0)

    segments = [
        {
            "id": "seg_001",
            "start_time": 0.0,
            "end_time": 5.0,
            "tags": [],
            "risk": "safe",
            "action": "play",
        },
        {
            "id": "seg_002",
            "start_time": 5.0,
            "end_time": 10.0,
            "tags": ["nudity"],
            "risk": "mature",
            "action": "swap",
        },
    ]

    first = analyzer._attach_goldylocks_fillers(segments)
    second = analyzer._attach_goldylocks_fillers(segments)

    for result in (first, second):
        mature = next(seg for seg in result if seg["id"] == "seg_002")
        filler = next(seg for seg in result if seg["id"] == "filler_001")
        assert mature["profile_segment_id"] == "filler_001"
        assert filler["is_filler"] is True
        assert filler["source_path"] == str(filler_video)
        assert filler["end_time"] == 5.0

    first_filler = next(seg for seg in first if seg["id"] == "filler_001")
    second_filler = next(seg for seg in second if seg["id"] == "filler_001")
    assert first_filler["source_start_time"] == second_filler["source_start_time"]
    assert first_filler["source_end_time"] == second_filler["source_end_time"]


def test_attach_goldylocks_fillers_warns_and_falls_back_when_missing(tmp_path: Path, caplog):
    analyzer = _build_analyzer(tmp_path)
    analyzer.goldylocks_filler_video = tmp_path / "missing_goldylocks.mp4"

    segments = [
        {
            "id": "seg_001",
            "start_time": 0.0,
            "end_time": 5.0,
            "tags": ["nudity"],
            "risk": "mature",
            "action": "swap",
        },
    ]

    result = analyzer._attach_goldylocks_fillers(segments)

    assert result == segments
    assert "Goldilocks filler video is missing" in caplog.text
