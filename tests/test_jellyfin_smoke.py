import sys
from io import BytesIO
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError

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
