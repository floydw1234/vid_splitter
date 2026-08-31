import sys
from io import BytesIO
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
import subprocess
import os

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT / "tools"

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import jellyfin_smoke


class _FakeHttpResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self) -> bytes:
        import json

        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _http_error(status_code: int, reason: str) -> HTTPError:
    return HTTPError(
        url=f"https://example.test/{status_code}",
        code=status_code,
        msg=reason,
        hdrs=None,
        fp=BytesIO(b""),
    )


def test_run_smoke_skips_when_jellyfin_url_and_auth_missing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("JELLYFIN_BASE_URL", raising=False)
    monkeypatch.delenv("JELLYFIN_API_KEY", raising=False)
    monkeypatch.delenv("JELLYFIN_USERNAME", raising=False)
    monkeypatch.delenv("JELLYFIN_PASSWORD", raising=False)
    monkeypatch.delenv("JELLYFIN_SHORTS_DIR", raising=False)

    with pytest.raises(pytest.skip.Exception, match="JELLYFIN_BASE_URL"):
        jellyfin_smoke.run_smoke()


def test_select_video_candidate_returns_skip_ready_result_when_no_default_or_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.delenv("JELLYFIN_SHORTS_DIR", raising=False)

    result = jellyfin_smoke.select_video_candidate(default_shorts_dir=tmp_path / "missing")

    assert result.path is None
    assert "JELLYFIN_SHORTS_DIR" in result.skip_reason


def test_select_video_candidate_prefers_override_with_video(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    override_dir = tmp_path / "shorts"
    override_dir.mkdir()
    video_path = override_dir / "clip.mp4"
    video_path.write_bytes(b"fake video bytes")

    monkeypatch.setenv("JELLYFIN_SHORTS_DIR", str(override_dir))

    result = jellyfin_smoke.select_video_candidate(default_shorts_dir=tmp_path / "missing")

    assert result.skip_reason is None
    assert result.path == video_path


def test_select_video_candidate_filters_extensions_and_uses_deterministic_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    override_dir = tmp_path / "shorts"
    override_dir.mkdir()
    (override_dir / "z-last.txt").write_text("ignore me", encoding="utf-8")
    first_video = override_dir / "a-first.mkv"
    second_video = override_dir / "b-second.mp4"
    first_video.write_bytes(b"video one")
    second_video.write_bytes(b"video two")

    monkeypatch.setenv("JELLYFIN_SHORTS_DIR", str(override_dir))

    result = jellyfin_smoke.select_video_candidate(default_shorts_dir=tmp_path / "missing")

    assert result.skip_reason is None
    assert result.path == first_video


def test_get_bvf_path_maps_movie_to_sibling_bvf():
    movie_path = Path("/path/Movie.mp4")

    assert jellyfin_smoke.get_bvf_path(movie_path) == Path("/path/Movie.bvf")


def test_build_analyzer_command_uses_existing_demo_branch_analyzer(tmp_path: Path):
    movie_path = tmp_path / "Movie.mp4"
    output_dir = tmp_path / "library"

    command = jellyfin_smoke.build_analyzer_command(movie_path, output_dir)

    assert command[:3] == [sys.executable, "analyzer/analyze.py", str(movie_path)]
    assert "--demo-branch" in command
    assert "--output-dir" in command
    assert command[-1] == str(output_dir)


def test_jellyfin_client_checks_public_system_info_for_liveness():
    client = jellyfin_smoke.JellyfinClient(
        jellyfin_smoke.SmokeConfig(
            jellyfin_base_url="https://jellyfin.example",
            jellyfin_api_key="secret",
            jellyfin_username=None,
            jellyfin_password=None,
            jellyfin_shorts_dir=None,
        )
    )

    with mock.patch.object(
        jellyfin_smoke,
        "urlopen",
        return_value=_FakeHttpResponse({"ServerName": "Jellyfin"}),
        create=True,
    ) as mocked_urlopen:
        payload = client.get_public_system_info()

    assert payload["ServerName"] == "Jellyfin"
    request = mocked_urlopen.call_args.args[0]
    assert request.full_url == "https://jellyfin.example/System/Info/Public"
    assert request.get_method() == "GET"


def test_build_api_key_headers_adds_authorization_token():
    headers = jellyfin_smoke.build_api_key_headers("secret-token")

    assert headers["Authorization"] == "MediaBrowser Token=\"secret-token\""
    assert headers["Accept"] == "application/json"


def test_jellyfin_client_logs_in_with_username_and_password():
    client = jellyfin_smoke.JellyfinClient(
        jellyfin_smoke.SmokeConfig(
            jellyfin_base_url="https://jellyfin.example/",
            jellyfin_api_key=None,
            jellyfin_username="william",
            jellyfin_password="s3cr3t",
            jellyfin_shorts_dir=None,
        )
    )

    with mock.patch.object(
        jellyfin_smoke,
        "urlopen",
        return_value=_FakeHttpResponse({"AccessToken": "session-token"}),
        create=True,
    ) as mocked_urlopen:
        token = client.authenticate()

    assert token == "session-token"
    request = mocked_urlopen.call_args.args[0]
    assert request.full_url == "https://jellyfin.example/Users/AuthenticateByName"
    assert request.get_method() == "POST"
    assert request.data is not None
    assert b'"Username": "william"' in request.data
    assert b'"Pw": "s3cr3t"' in request.data


def test_jellyfin_client_prefers_api_key_over_username_password():
    client = jellyfin_smoke.JellyfinClient(
        jellyfin_smoke.SmokeConfig(
            jellyfin_base_url="https://jellyfin.example",
            jellyfin_api_key="preferred-token",
            jellyfin_username="william",
            jellyfin_password="s3cr3t",
            jellyfin_shorts_dir=None,
        )
    )

    with mock.patch.object(client, "authenticate_with_password") as auth_password:
        headers = client.get_authenticated_headers()

    assert headers["Authorization"] == 'MediaBrowser Token="preferred-token"'
    auth_password.assert_not_called()


@pytest.mark.parametrize(
    ("status_code", "reason"),
    [
        (401, "Unauthorized"),
        (403, "Forbidden"),
        (503, "Server Busy"),
    ],
)
def test_jellyfin_client_raises_clear_errors_for_http_failures(status_code: int, reason: str):
    client = jellyfin_smoke.JellyfinClient(
        jellyfin_smoke.SmokeConfig(
            jellyfin_base_url="https://jellyfin.example",
            jellyfin_api_key="secret",
            jellyfin_username=None,
            jellyfin_password=None,
            jellyfin_shorts_dir=None,
        )
    )

    with mock.patch.object(
        jellyfin_smoke,
        "urlopen",
        side_effect=_http_error(status_code, reason),
        create=True,
    ):
        with pytest.raises(jellyfin_smoke.JellyfinApiError, match=str(status_code)):
            client.get_public_system_info()


def test_jellyfin_client_find_movie_item_prefers_bvf_path():
    client = jellyfin_smoke.JellyfinClient(
        jellyfin_smoke.SmokeConfig(
            jellyfin_base_url="https://jellyfin.example",
            jellyfin_api_key="secret",
            jellyfin_username=None,
            jellyfin_password=None,
            jellyfin_shorts_dir=None,
        )
    )
    movie_path = Path("/library/Movies/Movie.mp4")
    bvf_path = Path("/library/Movies/Movie.bvf")

    with mock.patch.object(
        client,
        "_request_json",
        return_value={
            "Items": [
                {"Id": "mp4", "Path": str(movie_path), "Name": "Movie"},
                {"Id": "bvf", "Path": str(bvf_path), "Name": "Movie"},
            ]
        },
    ):
        item = client.find_movie_item(movie_path, search_terms=["Movie"])

    assert item["Id"] == "bvf"


def test_jellyfin_client_find_movie_item_matches_exact_path():
    client = jellyfin_smoke.JellyfinClient(
        jellyfin_smoke.SmokeConfig(
            jellyfin_base_url="https://jellyfin.example",
            jellyfin_api_key="secret",
            jellyfin_username=None,
            jellyfin_password=None,
            jellyfin_shorts_dir=None,
        )
    )
    movie_path = Path("/library/Movies/Movie.mp4")

    with mock.patch.object(
        client,
        "_request_json",
        return_value={
            "Items": [
                {"Id": "wrong", "Path": "/library/Movies/Other.mp4", "Name": "Movie"},
                {"Id": "right", "Path": str(movie_path), "Name": "Movie"},
            ]
        },
    ) as request_json:
        item = client.find_movie_item(movie_path, search_terms=["Movie"])

    assert item["Id"] == "right"
    assert request_json.call_args.args[0:2] == ("GET", "/Items")
    assert request_json.call_args.kwargs["query"]["searchTerm"] == "Movie"


def test_jellyfin_client_find_movie_item_returns_none_when_no_match():
    client = jellyfin_smoke.JellyfinClient(
        jellyfin_smoke.SmokeConfig(
            jellyfin_base_url="https://jellyfin.example",
            jellyfin_api_key="secret",
            jellyfin_username=None,
            jellyfin_password=None,
            jellyfin_shorts_dir=None,
        )
    )

    with mock.patch.object(
        client,
        "_request_json",
        return_value={"Items": []},
    ):
        item = client.find_movie_item(Path("/library/Movies/Missing.mp4"), search_terms=["Missing"])

    assert item is None


def test_jellyfin_client_find_movie_item_raises_on_ambiguous_search_results():
    client = jellyfin_smoke.JellyfinClient(
        jellyfin_smoke.SmokeConfig(
            jellyfin_base_url="https://jellyfin.example",
            jellyfin_api_key="secret",
            jellyfin_username=None,
            jellyfin_password=None,
            jellyfin_shorts_dir=None,
        )
    )

    with mock.patch.object(
        client,
        "_request_json",
        return_value={
            "Items": [
                {"Id": "one", "Path": "/library/Movies/One.mp4", "Name": "Movie"},
                {"Id": "two", "Path": "/library/Movies/Two.mp4", "Name": "Movie"},
            ]
        },
    ):
        with pytest.raises(jellyfin_smoke.JellyfinApiError, match="Ambiguous"):
            client.find_movie_item(Path("/library/Movies/Movie.mp4"), search_terms=["Movie"])


def test_jellyfin_client_issue_refresh_prefers_item_refresh_when_item_is_known():
    client = jellyfin_smoke.JellyfinClient(
        jellyfin_smoke.SmokeConfig(
            jellyfin_base_url="https://jellyfin.example",
            jellyfin_api_key="secret",
            jellyfin_username=None,
            jellyfin_password=None,
            jellyfin_shorts_dir=None,
        )
    )

    with mock.patch.object(client, "_request_json", return_value={}) as request_json:
        client.issue_refresh(item_id="movie-123")

    assert request_json.call_args.args[0:2] == ("POST", "/Items/movie-123/Refresh")


def test_jellyfin_client_issue_refresh_falls_back_to_library_refresh_without_item():
    client = jellyfin_smoke.JellyfinClient(
        jellyfin_smoke.SmokeConfig(
            jellyfin_base_url="https://jellyfin.example",
            jellyfin_api_key="secret",
            jellyfin_username=None,
            jellyfin_password=None,
            jellyfin_shorts_dir=None,
        )
    )

    with mock.patch.object(client, "_request_json", return_value={}) as request_json:
        client.issue_refresh(item_id=None)

    assert request_json.call_args.args[0:2] == ("POST", "/Library/Refresh")


def test_jellyfin_client_waits_until_smart_branch_sources_appear():
    client = jellyfin_smoke.JellyfinClient(
        jellyfin_smoke.SmokeConfig(
            jellyfin_base_url="https://jellyfin.example",
            jellyfin_api_key="secret",
            jellyfin_username=None,
            jellyfin_password=None,
            jellyfin_shorts_dir=None,
        )
    )
    expected_sources = [{"Id": "smart-branch-child", "Name": "Smart Branch (child)"}]

    with mock.patch.object(
        client,
        "get_smart_branch_sources",
        side_effect=[[], [], expected_sources],
    ) as get_sources, mock.patch.object(jellyfin_smoke.time, "sleep") as sleep_mock:
        sources = client.wait_for_smart_branch_sources(
            item_id="movie-123",
            timeout_seconds=3.0,
            poll_interval_seconds=0.1,
        )

    assert sources == expected_sources
    assert get_sources.call_count == 3
    assert sleep_mock.call_count == 2


def test_jellyfin_client_wait_for_smart_branch_sources_raises_clear_timeout():
    client = jellyfin_smoke.JellyfinClient(
        jellyfin_smoke.SmokeConfig(
            jellyfin_base_url="https://jellyfin.example",
            jellyfin_api_key="secret",
            jellyfin_username=None,
            jellyfin_password=None,
            jellyfin_shorts_dir=None,
        )
    )

    with mock.patch.object(
        client,
        "get_smart_branch_sources",
        return_value=[],
    ), mock.patch.object(
        jellyfin_smoke.time,
        "monotonic",
        side_effect=[0.0, 0.2, 0.4, 0.6],
    ), mock.patch.object(jellyfin_smoke.time, "sleep"):
        with pytest.raises(jellyfin_smoke.JellyfinApiError, match="Timed out waiting for Smart Branch sources"):
            client.wait_for_smart_branch_sources(
                item_id="movie-123",
                timeout_seconds=0.5,
                poll_interval_seconds=0.1,
            )


def test_parse_smart_branch_discovery_extracts_profiles_from_mixed_media_sources():
    payload = {
        "MediaSources": [
            {"Id": "direct-1", "Name": "Movie", "Path": "/library/Movie.mp4"},
            {
                "Id": "sb-child",
                "Name": "Smart Branch (child)",
                "Path": "/library/Movie.bvf",
                "OpenToken": "smart-child-token",
            },
            {
                "Id": "sb-adult",
                "Name": "Smart Branch (adult)",
                "Path": "/library/Movie.bvf",
                "Container": "mp4",
            },
        ]
    }

    result = jellyfin_smoke.parse_smart_branch_discovery(payload)

    assert result["profiles"] == ["adult", "child"]
    assert [source["Id"] for source in result["sources"]] == ["sb-child", "sb-adult"]


def test_verify_smart_branch_discovery_raises_guidance_when_no_sources_found():
    client = jellyfin_smoke.JellyfinClient(
        jellyfin_smoke.SmokeConfig(
            jellyfin_base_url="https://jellyfin.example",
            jellyfin_api_key="secret",
            jellyfin_username=None,
            jellyfin_password=None,
            jellyfin_shorts_dir=None,
        )
    )

    with mock.patch.object(client, "issue_refresh", return_value={}) as issue_refresh, mock.patch.object(
        client,
        "wait_for_smart_branch_sources",
        return_value=[],
    ):
        with pytest.raises(jellyfin_smoke.JellyfinApiError, match="plugin logs"):
            client.verify_smart_branch_discovery(item_id="movie-123", timeout_seconds=1.0, poll_interval_seconds=0.1)

    issue_refresh.assert_called_once_with("movie-123")


def test_jellyfin_client_check_playback_readiness_posts_selected_media_source_id():
    client = jellyfin_smoke.JellyfinClient(
        jellyfin_smoke.SmokeConfig(
            jellyfin_base_url="https://jellyfin.example",
            jellyfin_api_key="secret",
            jellyfin_username=None,
            jellyfin_password=None,
            jellyfin_shorts_dir=None,
        )
    )
    discovery = {
        "profiles": ["child", "adult"],
        "sources": [
            {"Id": "sb-child", "Name": "Smart Branch (child)"},
            {"Id": "sb-adult", "Name": "Smart Branch (adult)"},
        ],
    }

    with mock.patch.object(
        client,
        "_request_json",
        return_value={"MediaSources": [{"Id": "playable-child"}]},
    ) as request_json:
        result = client.check_playback_readiness(item_id="movie-123", discovery=discovery, requested_profile="child")

    assert result.selected_profile == "child"
    assert result.selected_source_id == "sb-child"
    assert result.playable_media_source_count == 1
    assert request_json.call_args.args[0:2] == ("POST", "/Items/movie-123/PlaybackInfo")
    assert request_json.call_args.kwargs["body"]["MediaSourceId"] == "sb-child"


def test_jellyfin_client_check_playback_readiness_uses_default_source_when_profile_not_requested():
    client = jellyfin_smoke.JellyfinClient(
        jellyfin_smoke.SmokeConfig(
            jellyfin_base_url="https://jellyfin.example",
            jellyfin_api_key="secret",
            jellyfin_username=None,
            jellyfin_password=None,
            jellyfin_shorts_dir=None,
        )
    )
    discovery = {
        "profiles": ["adult", "child"],
        "sources": [
            {"Id": "sb-adult", "Name": "Smart Branch (adult)"},
            {"Id": "sb-child", "Name": "Smart Branch (child)"},
        ],
    }

    with mock.patch.object(
        client,
        "_request_json",
        return_value={"MediaSources": [{"Id": "playable-adult"}]},
    ):
        result = client.check_playback_readiness(item_id="movie-123", discovery=discovery)

    assert result.selected_profile == "adult"
    assert result.selected_source_id == "sb-adult"


def test_jellyfin_client_check_playback_readiness_raises_when_error_code_returned():
    client = jellyfin_smoke.JellyfinClient(
        jellyfin_smoke.SmokeConfig(
            jellyfin_base_url="https://jellyfin.example",
            jellyfin_api_key="secret",
            jellyfin_username=None,
            jellyfin_password=None,
            jellyfin_shorts_dir=None,
        )
    )
    discovery = {
        "profiles": ["child"],
        "sources": [{"Id": "sb-child", "Name": "Smart Branch (child)"}],
    }

    with mock.patch.object(
        client,
        "_request_json",
        return_value={"MediaSources": [], "ErrorCode": "NoCompatibleStream"},
    ):
        with pytest.raises(jellyfin_smoke.JellyfinApiError, match="NoCompatibleStream"):
            client.check_playback_readiness(item_id="movie-123", discovery=discovery, requested_profile="child")


def test_jellyfin_client_check_playback_readiness_raises_when_no_playable_media_sources_returned():
    client = jellyfin_smoke.JellyfinClient(
        jellyfin_smoke.SmokeConfig(
            jellyfin_base_url="https://jellyfin.example",
            jellyfin_api_key="secret",
            jellyfin_username=None,
            jellyfin_password=None,
            jellyfin_shorts_dir=None,
        )
    )
    discovery = {
        "profiles": ["child"],
        "sources": [{"Id": "sb-child", "Name": "Smart Branch (child)"}],
    }

    with mock.patch.object(
        client,
        "_request_json",
        return_value={"MediaSources": []},
    ):
        with pytest.raises(jellyfin_smoke.JellyfinApiError, match="No playable media sources"):
            client.check_playback_readiness(item_id="movie-123", discovery=discovery, requested_profile="child")


def test_main_prints_skip_style_output_and_nonzero_when_environment_is_missing(capsys: pytest.CaptureFixture[str]):
    with mock.patch.object(
        jellyfin_smoke,
        "run_smoke",
        side_effect=pytest.skip.Exception("Missing Jellyfin smoke configuration: JELLYFIN_BASE_URL"),
    ):
        exit_code = jellyfin_smoke.main()

    captured = capsys.readouterr()

    assert exit_code == 2
    assert "SKIPPED:" in captured.out
    assert "JELLYFIN_BASE_URL" in captured.out


def test_main_successful_dry_run_prints_concise_summary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    video_path = tmp_path / "Movie.mp4"
    video_path.write_bytes(b"video")
    bvf_path = tmp_path / "Movie.bvf"
    movie_item = {"Id": "movie-123", "Path": str(video_path), "Name": "Movie"}
    discovery = {
        "profiles": ["adult", "child"],
        "sources": [
            {"Id": "sb-adult", "Name": "Smart Branch (adult)"},
            {"Id": "sb-child", "Name": "Smart Branch (child)"},
        ],
    }
    readiness = jellyfin_smoke.PlaybackReadinessResult(
        selected_profile="adult",
        selected_source_id="sb-adult",
        playable_media_source_count=1,
    )

    monkeypatch.setenv("JELLYFIN_BASE_URL", "https://jellyfin.example")
    monkeypatch.setenv("JELLYFIN_API_KEY", "secret")

    with mock.patch.object(
        jellyfin_smoke,
        "select_video_candidate",
        return_value=jellyfin_smoke.VideoSelectionResult(path=video_path, skip_reason=None),
    ), mock.patch.object(
        jellyfin_smoke,
        "run_analyzer",
        return_value=bvf_path,
    ), mock.patch.object(
        jellyfin_smoke,
        "probe_bvf",
        return_value=None,
    ), mock.patch.object(
        jellyfin_smoke.JellyfinClient,
        "find_movie_item",
        return_value=movie_item,
    ), mock.patch.object(
        jellyfin_smoke.JellyfinClient,
        "verify_smart_branch_discovery",
        return_value=discovery,
    ), mock.patch.object(
        jellyfin_smoke.JellyfinClient,
        "check_playback_readiness",
        return_value=readiness,
    ):
        exit_code = jellyfin_smoke.main(["--dry-run"])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert f"Selected video: {video_path}" in captured.out
    assert f"Generated BVF: {bvf_path}" in captured.out
    assert "Discovered profiles: adult, child" in captured.out
    assert "Playback ready: yes (profile=adult, media_sources=1)" in captured.out


def test_main_invokes_demo_branch_analyzer_and_probe_before_jellyfin_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    video_path = tmp_path / "Movie.mp4"
    video_path.write_bytes(b"video")
    bvf_path = tmp_path / "Movie.bvf"
    call_order: list[str] = []

    monkeypatch.setenv("JELLYFIN_BASE_URL", "https://jellyfin.example")
    monkeypatch.setenv("JELLYFIN_API_KEY", "secret")

    def fake_find_movie_item(*args, **kwargs):
        call_order.append("find")
        return {"Id": "movie-123", "Path": str(video_path), "Name": "Movie"}

    with mock.patch.object(
        jellyfin_smoke,
        "select_video_candidate",
        return_value=jellyfin_smoke.VideoSelectionResult(path=video_path, skip_reason=None),
    ), mock.patch.object(
        jellyfin_smoke,
        "run_analyzer",
        side_effect=lambda *args, **kwargs: call_order.append("analyze") or bvf_path,
    ) as run_analyzer, mock.patch.object(
        jellyfin_smoke,
        "probe_bvf",
        side_effect=lambda *args, **kwargs: call_order.append("probe"),
    ) as probe_bvf, mock.patch.object(
        jellyfin_smoke.JellyfinClient,
        "find_movie_item",
        side_effect=fake_find_movie_item,
    ), mock.patch.object(
        jellyfin_smoke.JellyfinClient,
        "verify_smart_branch_discovery",
        return_value={"profiles": ["adult"], "sources": [{"Id": "sb-adult", "Name": "Smart Branch (adult)"}]},
    ), mock.patch.object(
        jellyfin_smoke.JellyfinClient,
        "check_playback_readiness",
        return_value=jellyfin_smoke.PlaybackReadinessResult(
            selected_profile="adult",
            selected_source_id="sb-adult",
            playable_media_source_count=1,
        ),
    ):
        exit_code = jellyfin_smoke.main(["--dry-run"])

    assert exit_code == 0
    assert "--demo-branch" in run_analyzer.call_args.args[0]
    assert probe_bvf.call_args.args[0] == bvf_path
    assert call_order == ["analyze", "probe", "find"]


def test_main_does_not_delete_or_mutate_unrelated_library_contents(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    video_path = library_dir / "Movie.mp4"
    video_path.write_bytes(b"video")
    unrelated_path = library_dir / "Keep.txt"
    unrelated_path.write_text("preserve me", encoding="utf-8")
    before_contents = unrelated_path.read_text(encoding="utf-8")

    monkeypatch.setenv("JELLYFIN_BASE_URL", "https://jellyfin.example")
    monkeypatch.setenv("JELLYFIN_API_KEY", "secret")

    with mock.patch.object(
        jellyfin_smoke,
        "select_video_candidate",
        return_value=jellyfin_smoke.VideoSelectionResult(path=video_path, skip_reason=None),
    ), mock.patch.object(
        jellyfin_smoke,
        "run_analyzer",
        return_value=tmp_path / "Movie.bvf",
    ), mock.patch.object(
        jellyfin_smoke,
        "probe_bvf",
        return_value=None,
    ), mock.patch.object(
        jellyfin_smoke.JellyfinClient,
        "find_movie_item",
        return_value={"Id": "movie-123", "Path": str(video_path), "Name": "Movie"},
    ), mock.patch.object(
        jellyfin_smoke.JellyfinClient,
        "verify_smart_branch_discovery",
        return_value={"profiles": ["adult"], "sources": [{"Id": "sb-adult", "Name": "Smart Branch (adult)"}]},
    ), mock.patch.object(
        jellyfin_smoke.JellyfinClient,
        "check_playback_readiness",
        return_value=jellyfin_smoke.PlaybackReadinessResult(
            selected_profile="adult",
            selected_source_id="sb-adult",
            playable_media_source_count=1,
        ),
    ), mock.patch.object(subprocess, "run") as subprocess_run:
        exit_code = jellyfin_smoke.main(["--dry-run"])

    assert exit_code == 0
    assert unrelated_path.read_text(encoding="utf-8") == before_contents
    assert unrelated_path.exists()
    subprocess_run.assert_not_called()


def test_real_jellyfin_smoke_workflow_opt_in():
    if os.environ.get("RUN_JELLYFIN_SMOKE") != "1":
        pytest.skip("set RUN_JELLYFIN_SMOKE=1 to run the real Jellyfin smoke workflow")

    config = jellyfin_smoke.load_smoke_config()
    try:
        jellyfin_smoke.require_prerequisites_or_skip(config)
    except pytest.skip.Exception as exc:
        pytest.skip(str(exc))

    shorts_dir = config.jellyfin_shorts_dir or str(jellyfin_smoke.DEFAULT_SHORTS_DIR)
    if not Path(shorts_dir).exists():
        pytest.skip(
            f"set JELLYFIN_SHORTS_DIR or ensure the default shorts directory exists: {shorts_dir}"
        )

    result = jellyfin_smoke.perform_smoke_workflow(
        timeout_seconds=60.0,
        poll_interval_seconds=2.0,
    )

    assert result.generated_bvf.exists()
    assert result.discovery["sources"]
    assert result.discovery["profiles"]
    assert result.playback_readiness.playable_media_source_count >= 1
