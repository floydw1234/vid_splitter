import json
from pathlib import Path

from analyzer.analyze import MovieAnalyzer


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
            "triggered_by": triggered_by or [],
            "brightened_rescue_applied": brightened_rescue_applied,
            "brightened_rescue_triggered_by": brightened_rescue_triggered_by or [],
            "threshold": threshold,
            "threshold_passed": threshold_passed,
            "sd_has_nsfw": "stable_diffusion" in (triggered_by or []),
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
    )

    assert result["triggered_by"] == []
    assert result["threshold_passed"] is False
    assert result["falcon_confidence"] == 0.49


def test_combine_nudity_signals_preserves_debug_fields_for_passed_frame(tmp_path: Path):
    analyzer = _build_analyzer(tmp_path)

    result = analyzer._combine_nudity_signals(
        threshold=0.7,
        sd_confidence=0.82,
        sd_has_nsfw=True,
        falcon_confidence=0.56,
    )

    assert result["score"] == 0.82
    assert result["triggered_by"] == ["stable_diffusion", "falcon"]
    assert result["threshold"] == 0.7
    assert result["threshold_passed"] is True


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
            brightened_rescue_applied=True,
        ),
        _scan_result(
            277.5,
            score=0.7944,
            threshold_passed=False,
            sd_confidence=0.0,
            falcon_confidence=0.0,
            brightened_rescue_applied=True,
        ),
        _scan_result(
            278.0,
            score=0.8147,
            threshold_passed=False,
            sd_confidence=0.0,
            falcon_confidence=0.0,
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


def test_attach_goldylocks_skips_segments_already_replaced_by_wan(tmp_path: Path, monkeypatch):
    analyzer = _build_analyzer(tmp_path)
    filler_video = tmp_path / "goldylocks.mp4"
    filler_video.write_bytes(b"not-a-real-video")
    analyzer.goldylocks_filler_video = filler_video
    monkeypatch.setattr(analyzer, "_get_duration_for_path", lambda path: 30.0)

    segments = [
        {
            "id": "seg_001",
            "start_time": 5.0,
            "end_time": 15.0,
            "tags": ["nudity"],
            "risk": "mature",
            "action": "swap",
            "profile_segment_id": "filler_001",
            "wan_replaced": True,
        },
        {
            "id": "filler_001",
            "start_time": 0.0,
            "end_time": 10.0,
            "tags": [],
            "risk": "safe",
            "action": "play",
            "is_filler": True,
            "source_path": str(tmp_path / "wan.mp4"),
        },
    ]

    result = analyzer._attach_goldylocks_fillers(segments)
    assert [seg["id"] for seg in result] == ["seg_001", "filler_001"]


def _tiny_mp4(path, duration=1.0):
    import subprocess

    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s=64x64:d={duration:.3f}",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-shortest",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return path


def test_apply_wan_replacements_coalesces_and_attaches_one_filler(tmp_path: Path):
    video = tmp_path / "movie.mp4"
    _tiny_mp4(video, 1.0)

    def fake_generate(*, prompt, output, video, start, duration, seed, keep_audio, image=None):
        assert "modest clothing" in prompt
        return _tiny_mp4(output, duration)

    analyzer = _build_analyzer(
        tmp_path,
        wan_replace=True,
        wan_generate=fake_generate,
        filter_topics=("nudity",),
    )
    analyzer.video_path = video
    analyzer.output_dir = tmp_path
    analyzer.goldylocks_filler_video = tmp_path / "missing_goldylocks.mp4"

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
        {
            "id": "seg_003",
            "start_time": 10.0,
            "end_time": 15.0,
            "tags": ["nudity"],
            "risk": "mature",
            "action": "swap",
        },
    ]

    result = analyzer._apply_wan_replacements(segments)
    ids = [seg["id"] for seg in result]
    assert "seg_003" not in ids
    mature = next(seg for seg in result if seg["id"] == "seg_002")
    filler = next(seg for seg in result if seg["id"] == "filler_001")
    assert mature["end_time"] == 15.0
    assert mature["profile_segment_id"] == "filler_001"
    assert mature["wan_replaced"] is True
    assert filler["is_filler"] is True
    assert (tmp_path / "movie_wan_jobs.json").is_file()

    profiles = analyzer._profiles_for_manifest(result)
    assert profiles["child"]["filters"]["nudity"] == "swap"
    assert profiles["adult"]["filters"] == {}


def test_apply_wan_replacements_reuses_existing_chunk_files(tmp_path: Path):
    video = tmp_path / "movie.mp4"
    _tiny_mp4(video, 1.0)
    clips = tmp_path / "movie_wan_clips"
    clips.mkdir()
    _tiny_mp4(clips / "run_001_chunk_00.mp4", 5.0)

    def fake_generate(**kwargs):
        raise AssertionError("should reuse the on-disk chunk")

    analyzer = _build_analyzer(
        tmp_path,
        wan_replace=True,
        wan_generate=fake_generate,
        filter_topics=("nudity",),
    )
    analyzer.video_path = video
    analyzer.output_dir = tmp_path

    result = analyzer._apply_wan_replacements(
        [
            {
                "id": "seg_002",
                "start_time": 5.0,
                "end_time": 10.0,
                "tags": ["nudity"],
                "risk": "mature",
                "action": "swap",
            }
        ]
    )
    mature = next(seg for seg in result if seg["id"] == "seg_002")
    assert mature["wan_replaced"] is True
    assert (clips / "run_001.mp4").is_file()


def test_wan_dry_run_writes_plan_without_changing_segments(tmp_path: Path):
    analyzer = _build_analyzer(tmp_path, wan_replace=True, wan_dry_run=True, filter_topics=("nudity",))
    analyzer.output_dir = tmp_path
    segments = [
        {
            "id": "seg_002",
            "start_time": 5.0,
            "end_time": 10.0,
            "tags": ["nudity"],
            "risk": "mature",
            "action": "swap",
        },
    ]
    result = analyzer._apply_wan_replacements(segments)
    assert result == segments
    plan = json.loads((tmp_path / "movie_wan_jobs.json").read_text())
    assert plan["dry_run"] is True
    assert plan["jobs"][0]["segment_ids"] == ["seg_002"]


def test_apply_wan_replacements_honors_segment_filter_and_max_run(tmp_path: Path):
    video = tmp_path / "movie.mp4"
    _tiny_mp4(video, 1.0)
    calls = []

    def fake_generate(*, prompt, output, video, start, duration, seed, keep_audio, image=None):
        calls.append((start, duration))
        return _tiny_mp4(output, duration)

    analyzer = _build_analyzer(
        tmp_path,
        wan_replace=True,
        wan_segments=("seg_003",),
        wan_max_run=4.0,
        wan_overlap=0.5,
        wan_generate=fake_generate,
        filter_topics=("nudity",),
    )
    analyzer.video_path = video
    analyzer.output_dir = tmp_path
    analyzer.goldylocks_filler_video = tmp_path / "missing.mp4"

    segments = [
        {
            "id": "seg_002",
            "start_time": 5.0,
            "end_time": 10.0,
            "tags": ["nudity"],
            "risk": "mature",
            "action": "swap",
        },
        {
            "id": "seg_003",
            "start_time": 10.0,
            "end_time": 18.0,
            "tags": ["nudity"],
            "risk": "mature",
            "action": "swap",
        },
    ]
    result = analyzer._apply_wan_replacements(segments)
    assert [seg["id"] for seg in result if not seg.get("is_filler")] == ["seg_002", "seg_003"]
    mature = next(seg for seg in result if seg["id"] == "seg_003")
    leftover = next(seg for seg in result if seg["id"] == "seg_002")
    assert leftover.get("profile_segment_id") is None
    assert mature["profile_segment_id"] == "filler_001"
    assert len(calls) == 3
    assert calls[0][0] == 10.0


def test_extra_profiles_are_baked_into_manifest(tmp_path: Path):
    from analyzer.profiles import load_profile_file

    analyzer = _build_analyzer(
        tmp_path,
        extra_profiles=load_profile_file(
            Path(__file__).resolve().parents[1] / "examples" / "strict_parent.json"
        ),
        classify_topics=False,
    )
    profiles = analyzer._profiles_for_manifest([])
    assert profiles["strict_parent"]["filters"]["religion_christianity"] == "skip"
    assert profiles["strict_parent"]["filters"]["nudity"] == "skip"
    assert "child" in profiles


def test_replace_mode_swap_rewrites_every_filter_value(tmp_path: Path):
    from analyzer.profiles import load_profile_file

    analyzer = _build_analyzer(
        tmp_path,
        replace_mode="swap",
        extra_profiles=load_profile_file(
            Path(__file__).resolve().parents[1] / "examples" / "strict_parent.json"
        ),
        classify_topics=False,
        visual_topics=False,
    )
    profiles = analyzer._profiles_for_manifest([
        {"id": "seg_001", "wan_replaced": True, "tags": ["nudity"]},
    ])
    assert profiles["strict_parent"]["filters"]["religion_christianity"] == "swap"
    assert profiles["strict_parent"]["filters"]["nudity"] == "swap"
    assert profiles["child"]["filters"]["language"] == "swap"
    assert profiles["adult"]["filters"] == {}


def test_skip_mode_does_not_plan_wan_jobs(tmp_path: Path):
    analyzer = _build_analyzer(tmp_path, replace_mode="skip")
    analyzer.output_dir = tmp_path
    segments = [
        {
            "id": "seg_002",
            "start_time": 5.0,
            "end_time": 10.0,
            "tags": ["nudity"],
            "topics": [],
            "risk": "mature",
            "action": "skip",
        },
    ]
    assert analyzer._apply_wan_replacements(segments) == segments
    assert not (tmp_path / "movie_wan_jobs.json").exists()


def test_apply_replace_mode_uses_tags_and_topics(tmp_path: Path):
    from analyzer.profiles import load_profile_file

    analyzer = _build_analyzer(
        tmp_path,
        replace_mode="skip",
        extra_profiles=load_profile_file(
            Path(__file__).resolve().parents[1] / "examples" / "strict_parent.json"
        ),
        visual_topics=False,
        filter_topics=("religion_christianity", "nudity"),
    )
    result = analyzer._apply_replace_mode([
        {"id": "seg_001", "tags": [], "topics": ["religion_christianity"]},
        {"id": "seg_002", "tags": ["nudity"], "topics": []},
        {"id": "seg_003", "tags": [], "topics": []},
    ])
    assert [seg["action"] for seg in result] == ["skip", "skip", "play"]


def test_wan_prompt_includes_cross_and_nudity(tmp_path: Path):
    from analyzer.profiles import load_profile_file

    video = tmp_path / "movie.mp4"
    _tiny_mp4(video, 1.0)
    prompts = []

    def fake_generate(*, prompt, output, video, start, duration, seed, keep_audio, image=None):
        prompts.append(prompt)
        return _tiny_mp4(output, duration)

    analyzer = _build_analyzer(
        tmp_path,
        replace_mode="swap",
        extra_profiles=load_profile_file(
            Path(__file__).resolve().parents[1] / "examples" / "strict_parent.json"
        ),
        wan_prompt="do not add new characters",
        wan_generate=fake_generate,
        visual_topics=False,
        filter_topics=("nudity", "religion_christianity"),
    )
    analyzer.video_path = video
    analyzer.output_dir = tmp_path
    result = analyzer._apply_wan_replacements([
        {
            "id": "seg_002",
            "start_time": 5.0,
            "end_time": 10.0,
            "tags": ["nudity"],
            "topics": ["religion_christianity"],
            "risk": "mature",
            "action": "swap",
        },
    ])
    assert prompts
    assert "Christian" in prompts[0]
    assert "nudity" in prompts[0].lower()
    assert prompts[0].endswith("do not add new characters")
    assert any(seg.get("wan_replaced") for seg in result)


class _FakeTopicClassifier:
    def classify_segments(self, segments):
        for seg in segments:
            text = seg.get("transcript", "")
            seg["topics"] = ["religion_christianity"] if "jesus" in text.lower() else []
        return segments


def test_classify_topics_labels_many_speech_buckets(tmp_path: Path):
    analyzer = _build_analyzer(
        tmp_path,
        classify_topics=True,
        topic_classifier=_FakeTopicClassifier(),
    )
    analyzer._transcript_data = {
        "segments": [
            {
                "words": [
                    {"word": "hello", "start": 0.0, "end": 1.0},
                    {"word": "jesus", "start": 5.2, "end": 5.8},
                    {"word": "saves", "start": 6.0, "end": 6.5},
                ]
            }
        ]
    }
    segments = [
        {"id": f"seg_{i:03d}", "start_time": float(i * 5), "end_time": float(i * 5 + 5), "tags": []}
        for i in range(45)
    ]
    classified = analyzer._classify_topics(segments)
    assert classified[1]["topics"] == ["religion_christianity"]
    assert classified[0]["topics"] == []
    assert all("topics" in seg for seg in classified)


def test_apply_visual_topics_uses_injected_scanner(tmp_path: Path):
    from PIL import Image

    frame = tmp_path / "broad_0000_0000002.000.jpg"
    Image.new("RGB", (8, 8), color="white").save(frame)

    class Scanner:
        def topics_for_path(self, path):
            return ["religion_christianity"]

    analyzer = _build_analyzer(
        tmp_path,
        visual_topic_scanner=Scanner(),
        classify_topics=False,
    )
    analyzer._scanned_frames = [{"time": 2.0, "frame_path": str(frame)}]
    result = analyzer._apply_visual_topics([
        {"id": "seg_001", "start_time": 0.0, "end_time": 5.0, "topics": []},
    ])
    assert result[0]["topics"] == ["religion_christianity"]

