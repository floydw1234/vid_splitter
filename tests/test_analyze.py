from pathlib import Path

from analyzer.analyze import MovieAnalyzer
from analyzer.skin_detector import SkinDetector


def _build_analyzer(tmp_path: Path, **kwargs) -> MovieAnalyzer:
    return MovieAnalyzer(str(tmp_path / "movie.mp4"), load_models=False, **kwargs)


def _scan_result(
    timestamp: float,
    *,
    score: float,
    threshold_passed: bool,
    sd_confidence: float,
    falcon_confidence: float,
    triggered_by: list[str] | None = None,
    skin_confidence: float = 0.0,
    skin_has_nsfw: bool = False,
    skin_ratio: float = 0.0,
    max_contour_ratio: float = 0.0,
    threshold: float = 0.75,
    brightened_rescue_applied: bool = False,
    brightened_rescue_triggered_by: list[str] | None = None,
) -> dict:
    return {
        "time": timestamp,
        "phase": "dense",
        "frame_path": f"/tmp/frame_{timestamp:.3f}.jpg",
        "media_type": "live_action",
        "is_cartoon": False,
        "classification": {
            "score": score,
            "sd_confidence": sd_confidence,
            "falcon_confidence": falcon_confidence,
            "skin_confidence": skin_confidence,
            "skin_ratio": skin_ratio,
            "max_contour_ratio": max_contour_ratio,
            "triggered_by": triggered_by or [],
            "brightened_rescue_applied": brightened_rescue_applied,
            "brightened_rescue_triggered_by": brightened_rescue_triggered_by or [],
            "threshold": threshold,
            "threshold_passed": threshold_passed,
            "sd_has_nsfw": "stable_diffusion" in (triggered_by or []),
            "skin_has_nsfw": skin_has_nsfw,
        },
    }


def test_default_thresholds_are_more_conservative(tmp_path: Path):
    analyzer = _build_analyzer(tmp_path)

    assert analyzer.nsfw_threshold == 0.75
    assert analyzer.cartoon_threshold == 0.8
    assert analyzer.scan_interval == analyzer.frame_interval
    assert analyzer.candidate_threshold == 0.25
    assert analyzer.min_positive_frames == 2


def test_combine_nudity_signals_ignores_low_confidence_falcon(tmp_path: Path):
    analyzer = _build_analyzer(tmp_path)

    result = analyzer._combine_nudity_signals(
        threshold=0.7,
        sd_confidence=0.0,
        sd_has_nsfw=False,
        falcon_confidence=0.49,
        skin_confidence=0.0,
        skin_has_nsfw=False,
        skin_ratio=0.0,
        max_contour_ratio=0.0,
    )

    assert result["triggered_by"] == []
    assert result["threshold_passed"] is False
    assert result["falcon_confidence"] == 0.49


def test_combine_nudity_signals_passes_on_strict_skin_only_signal(tmp_path: Path):
    analyzer = _build_analyzer(tmp_path)

    result = analyzer._combine_nudity_signals(
        threshold=0.8,
        sd_confidence=0.0,
        sd_has_nsfw=False,
        falcon_confidence=0.1,
        skin_confidence=0.97,
        skin_has_nsfw=True,
        skin_ratio=0.65,
        max_contour_ratio=0.35,
    )

    assert result["triggered_by"] == ["skin"]
    assert result["threshold_passed"] is True
    assert result["score"] == 0.97
    assert result["skin_has_nsfw"] is True
    assert result["skin_confidence"] == 0.97
    assert result["skin_ratio"] == 0.65
    assert result["max_contour_ratio"] == 0.35


def test_combine_nudity_signals_does_not_pass_on_subthreshold_skin_only_signal(tmp_path: Path):
    analyzer = _build_analyzer(tmp_path)

    result = analyzer._combine_nudity_signals(
        threshold=0.8,
        sd_confidence=0.0,
        sd_has_nsfw=False,
        falcon_confidence=0.1,
        skin_confidence=0.79,
        skin_has_nsfw=True,
        skin_ratio=0.65,
        max_contour_ratio=0.35,
    )

    assert result["triggered_by"] == ["skin"]
    assert result["threshold_passed"] is False
    assert result["score"] == 0.79
    assert result["skin_has_nsfw"] is True


def test_combine_nudity_signals_preserves_debug_fields_for_passed_frame(tmp_path: Path):
    analyzer = _build_analyzer(tmp_path)

    result = analyzer._combine_nudity_signals(
        threshold=0.7,
        sd_confidence=0.82,
        sd_has_nsfw=True,
        falcon_confidence=0.56,
        skin_confidence=0.74,
        skin_has_nsfw=True,
        skin_ratio=0.48,
        max_contour_ratio=0.21,
    )

    assert result["score"] == 0.82
    assert result["triggered_by"] == ["stable_diffusion", "falcon", "skin"]
    assert result["threshold"] == 0.7
    assert result["threshold_passed"] is True
    assert result["skin_confidence"] == 0.74
    assert result["skin_ratio"] == 0.48
    assert result["max_contour_ratio"] == 0.21


def test_resolve_debug_contact_sheet_path_handles_relative_output_dir_prefixes(tmp_path: Path):
    analyzer = _build_analyzer(
        tmp_path,
        output_dir="output/run",
        debug_contact_sheet="output/run/goldilocks_debug_contact_sheet.png",
    )

    assert analyzer._resolve_debug_contact_sheet_path() == Path(
        "output/run/goldilocks_debug_contact_sheet.png"
    )

    analyzer.debug_contact_sheet = Path("goldilocks_debug_contact_sheet")
    assert analyzer._resolve_debug_contact_sheet_path() == Path(
        "output/run/goldilocks_debug_contact_sheet.png"
    )

    analyzer.debug_contact_sheet = Path("debug/goldilocks_debug_contact_sheet.png")
    assert analyzer._resolve_debug_contact_sheet_path() == Path(
        "output/run/debug/goldilocks_debug_contact_sheet.png"
    )


def test_skin_detector_uses_stricter_region_thresholds():
    confidence, has_nsfw = SkinDetector._evaluate_skin_regions(0.59, 0.35)

    assert confidence > 0.0
    assert has_nsfw is False


def test_skin_detector_can_still_flag_large_regions():
    confidence, has_nsfw = SkinDetector._evaluate_skin_regions(0.65, 0.35)

    assert confidence >= 0.7
    assert has_nsfw is True


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


def test_merge_candidate_windows_pads_clamps_and_merges(tmp_path: Path):
    analyzer = _build_analyzer(tmp_path, dense_window_padding=2.0)

    windows = analyzer._merge_candidate_windows(
        [
            {"time": 0.5},
            {"time": 4.0},
            {"time": 19.5},
        ],
        duration=20.0,
    )

    assert windows == [(0.0, 6.0), (17.5, 20.0)]


def test_dense_window_detection_requires_min_positive_frames(tmp_path: Path, monkeypatch):
    analyzer = _build_analyzer(tmp_path, min_positive_frames=2, dense_rescan_fps=2.0)
    window = (9.0, 12.0)

    dense_results = [
        _scan_result(
            9.0,
            score=0.32,
            threshold_passed=False,
            sd_confidence=0.0,
            falcon_confidence=0.32,
        ),
        _scan_result(
            10.0,
            score=0.92,
            threshold_passed=True,
            sd_confidence=0.92,
            falcon_confidence=0.55,
            triggered_by=["stable_diffusion", "falcon"],
        ),
        _scan_result(
            10.5,
            score=0.1,
            threshold_passed=False,
            sd_confidence=0.0,
            falcon_confidence=0.1,
            skin_confidence=0.98,
            skin_has_nsfw=True,
            skin_ratio=0.67,
            max_contour_ratio=0.34,
        ),
    ]

    assert analyzer._build_dense_window_detection(
        window=window,
        dense_results=dense_results,
        duration=20.0,
        frames_dir=tmp_path,
    ) is None

    def _fake_boundary(known_bad_time: float, _duration: float, *, backward: bool, **kwargs) -> float:
        if backward:
            return round(max(kwargs["search_start"], known_bad_time - 0.1), 2)
        return round(min(kwargs["search_end"], known_bad_time + 0.1), 2)

    monkeypatch.setattr(analyzer, "_binary_search_boundary", _fake_boundary)

    dense_results.append(
        _scan_result(
            11.0,
            score=0.88,
            threshold_passed=True,
            sd_confidence=0.88,
            falcon_confidence=0.58,
            triggered_by=["stable_diffusion", "falcon"],
        )
    )

    detection = analyzer._build_dense_window_detection(
        window=window,
        dense_results=dense_results,
        duration=20.0,
        frames_dir=tmp_path,
    )

    assert detection is not None
    assert detection["phase"] == "dense"
    assert detection["positive_frames"] == 2
    assert detection["positive_timestamps"] == [10.0, 11.0]
    assert detection["bad_start"] == 9.9
    assert detection["bad_end"] == 11.1
    assert detection["triggered_by"] == ["stable_diffusion", "falcon"]


def test_dense_window_detection_extends_boundary_to_dark_scene_rescue_near_misses(
    tmp_path: Path, monkeypatch
):
    analyzer = _build_analyzer(tmp_path, min_positive_frames=2, dense_rescan_fps=2.0)
    window = (272.0, 284.0)

    dense_results = [
        _scan_result(
            277.0,
            score=0.8069,
            threshold_passed=False,
            sd_confidence=0.0,
            falcon_confidence=0.0,
            skin_confidence=0.8069,
            brightened_rescue_applied=True,
        ),
        _scan_result(
            277.5,
            score=0.7944,
            threshold_passed=False,
            sd_confidence=0.0,
            falcon_confidence=0.0,
            skin_confidence=0.7944,
            brightened_rescue_applied=True,
        ),
        _scan_result(
            278.0,
            score=0.8147,
            threshold_passed=False,
            sd_confidence=0.0,
            falcon_confidence=0.0,
            skin_confidence=0.8147,
            brightened_rescue_applied=True,
        ),
        _scan_result(
            281.0,
            score=0.7696,
            threshold_passed=True,
            sd_confidence=0.5,
            falcon_confidence=0.0,
            triggered_by=["stable_diffusion"],
            brightened_rescue_applied=True,
            brightened_rescue_triggered_by=["stable_diffusion"],
        ),
        _scan_result(
            281.5,
            score=0.7701,
            threshold_passed=True,
            sd_confidence=0.51,
            falcon_confidence=0.0,
            triggered_by=["stable_diffusion"],
            brightened_rescue_applied=True,
            brightened_rescue_triggered_by=["stable_diffusion"],
        ),
    ]

    def _fake_boundary(known_bad_time: float, _duration: float, *, backward: bool, **kwargs) -> float:
        if backward:
            return round(max(kwargs["search_start"], known_bad_time - 0.1), 2)
        return round(min(kwargs["search_end"], known_bad_time + 0.1), 2)

    monkeypatch.setattr(analyzer, "_binary_search_boundary", _fake_boundary)

    detection = analyzer._build_dense_window_detection(
        window=window,
        dense_results=dense_results,
        duration=600.0,
        frames_dir=tmp_path,
    )

    assert detection is not None
    assert detection["positive_frames"] == 2
    assert detection["positive_timestamps"] == [281.0, 281.5]
    assert detection["bad_start"] == 276.9
    assert detection["bad_end"] == 281.6


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
