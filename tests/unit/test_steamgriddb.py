"""Phase 8 SteamGridDB client: search, assets, retries, download validation."""

from __future__ import annotations

import json

import pytest
import requests

from steam_desktop_importer.steamgriddb import (
    ENV_KEY,
    AuthenticationError,
    GameResult,
    GridAsset,
    InvalidResponseError,
    MissingAPIKeyError,
    NotFoundError,
    RateLimitError,
    SteamGridDBClient,
    SteamGridDBTimeoutError,
    asset_download_url,
    preview_download_url,
    clear_stored_api_key,
    default_key_path,
    resolve_api_key,
    save_api_key,
    search_queries,
    sniff_image,
    unrecognised_image_reason,
)
from steam_desktop_importer.steamgriddb.download import DEFAULT_MAX_BYTES, download_url

PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)
WEBP_HEADER = b"RIFF" + (12).to_bytes(4, "little") + b"WEBP" + b"\x00" * 4


class FakeResponse:
    def __init__(
        self,
        status: int,
        json_body: object | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        url: str = "https://www.steamgriddb.com/api/v2/search/autocomplete/Kate",
    ) -> None:
        self.status_code = status
        self._json = json_body
        if content is not None:
            self.content = content
        elif json_body is not None:
            self.content = json.dumps(json_body).encode("utf-8")
        else:
            self.content = b""
        self.headers = headers or {}
        self.url = url
        self.closed = False

    def json(self):
        if self._json is None:
            raise ValueError("not json")
        return self._json

    def close(self) -> None:
        self.closed = True
        return None

    def iter_content(self, chunk_size: int = 1):
        yield self.content


class ScriptedSession:
    def __init__(self, script: list[object]) -> None:
        self.script = list(script)
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []
        self.sent_headers: list[dict[str, str]] = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url))
        self.sent_headers.append(dict(kwargs.get("headers") or {}))
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def prepare_request(self, request: requests.Request):
        prepared = requests.Session()
        prepared.headers.update(self.headers)
        return prepared.prepare_request(request)

    def send(self, prepared, **kwargs):
        self.sent_headers.append(dict(prepared.headers))
        return self.request(prepared.method or "GET", prepared.url)

    def close(self) -> None:
        return None


def _client(script: list[object], **kwargs) -> SteamGridDBClient:
    session = ScriptedSession(script)
    client = SteamGridDBClient("test-secret-key", session=session, sleeper=lambda _delay: None, **kwargs)
    return client


def test_search_returns_games():
    client = _client(
        [
            FakeResponse(
                200,
                {
                    "success": True,
                    "data": [
                        {
                            "id": 2254,
                            "name": "Half-Life 2",
                            "types": ["steam"],
                            "verified": True,
                        }
                    ],
                },
            )
        ]
    )
    games = client.search_games("Half-Life 2")
    assert games == [
        GameResult(id=2254, name="Half-Life 2", types=("steam",), verified=True)
    ]
    assert "Half-Life%202" in client.session.calls[0][1]
    assert client.session.headers["Authorization"] == "Bearer test-secret-key"


def test_search_no_results_is_empty_not_an_error():
    client = _client([FakeResponse(200, {"success": True, "data": []})])
    assert client.search_games("zzzz-no-such-game") == []


def test_search_encodes_the_path_segment():
    client = _client([FakeResponse(200, {"success": True, "data": []})])
    client.search_games("Kate & Konsole")
    assert "Kate%20%26%20Konsole" in client.session.calls[0][1]


def test_401_is_authentication_error():
    client = _client([FakeResponse(401, {"success": False, "errors": ["Unauthorized"]})])
    with pytest.raises(AuthenticationError) as caught:
        client.search_games("Kate")
    assert "test-secret-key" not in str(caught.value)
    assert caught.value.status_code == 401


def test_404_is_not_found():
    client = _client([FakeResponse(404, {"success": False, "errors": ["Game not found."]})])
    with pytest.raises(NotFoundError, match="Game not found"):
        client.get_grids(0)


def test_429_retries_then_succeeds():
    slept: list[float] = []
    session = ScriptedSession(
        [
            FakeResponse(429, {"success": False, "errors": ["Slow down"]}, headers={"Retry-After": "0"}),
            FakeResponse(200, {"success": True, "data": []}),
        ]
    )
    client = SteamGridDBClient(
        "test-secret-key",
        session=session,
        sleeper=slept.append,
    )
    assert client.search_games("Kate") == []
    assert slept == [0.0]
    assert len(session.calls) == 2


def test_429_exhausted_raises_rate_limit():
    session = ScriptedSession(
        [FakeResponse(429, {}, headers={"Retry-After": "0"})] * 4
    )
    client = SteamGridDBClient("k", session=session, sleeper=lambda _delay: None, max_retries=3)
    with pytest.raises(RateLimitError):
        client.search_games("Kate")
    assert len(session.calls) == 4


def test_timeout_is_retried_then_raised():
    session = ScriptedSession(
        [requests.Timeout("read timed out"), requests.Timeout("read timed out")]
    )
    client = SteamGridDBClient(
        "k", session=session, sleeper=lambda _delay: None, max_retries=1
    )
    with pytest.raises(SteamGridDBTimeoutError):
        client.search_games("Kate")


def test_grids_heroes_logos_icons(monkeypatch):
    assets = {
        "success": True,
        "data": [
            {
                "id": 80,
                "score": 1,
                "style": "alternate",
                "url": "https://cdn.example/grid.png",
                "thumb": "https://cdn.example/thumb.png",
                "width": 600,
                "height": 900,
                "author": {"name": "Artist"},
            }
        ],
    }
    client = _client([FakeResponse(200, assets)] * 4)
    grids = client.get_grids(2254, dimensions=["600x900"])
    heroes = client.get_heroes(2254)
    logos = client.get_logos(2254)
    icons = client.get_icons(2254)
    assert [item.kind for item in (grids[0], heroes[0], logos[0], icons[0])] == [
        "grid",
        "hero",
        "logo",
        "icon",
    ]
    recorded = {}

    def capture(method, url, **kwargs):
        recorded["params"] = kwargs.get("params")
        return FakeResponse(200, {"success": True, "data": []})

    client.session.request = capture
    client.get_grids(1, dimensions=["600x900", "342x482"], styles=["material"])
    assert recorded["params"]["dimensions"] == "600x900,342x482"
    assert recorded["params"]["styles"] == "material"
    assert recorded["params"]["nsfw"] == "false"
    assert recorded["params"]["humor"] == "false"
    assert recorded["params"]["epilepsy"] == "false"
    assert recorded["params"]["types"] == "static"


def test_listing_follows_pagination():
    def page_payload(ids: list[int], page: int, total: int = 3):
        return FakeResponse(
            200,
            {
                "success": True,
                "page": page,
                "limit": 2,
                "total": total,
                "data": [
                    {"id": item, "url": f"https://cdn.example/{item}.png"} for item in ids
                ],
            },
        )

    client = _client([page_payload([1, 2], 0), page_payload([3], 1)])
    grids = client.get_grids(1)
    assert [asset.id for asset in grids] == [1, 2, 3]
    assert client.session.script == []
    assert len(client.session.calls) == 2


def test_listing_filters_opt_into_nsfw_humor_and_animated():
    from steam_desktop_importer.steamgriddb import ArtworkFilters

    recorded = {}

    def capture(method, url, **kwargs):
        recorded["params"] = kwargs.get("params")
        return FakeResponse(200, {"success": True, "data": []})

    client = _client([])
    client.session.request = capture
    filters = ArtworkFilters.from_allows(nsfw=True, humor=True, animated=True, static=True)
    client.get_heroes(9, filters=filters)
    assert recorded["params"]["nsfw"] == "any"
    assert recorded["params"]["humor"] == "any"
    assert recorded["params"]["types"] == "static,animated"
    assert "styles" not in recorded["params"]


def test_asset_parses_nsfw_humor_and_animated_type():
    client = _client(
        [
            FakeResponse(
                200,
                {
                    "success": True,
                    "data": [
                        {
                            "id": 3,
                            "url": "https://cdn.example/a.webp",
                            "nsfw": True,
                            "humor": True,
                            "epilepsy": True,
                            "type": "animated",
                        }
                    ],
                },
            )
        ]
    )
    asset = client.get_grids(1)[0]
    assert asset.nsfw is True
    assert asset.humor is True
    assert asset.epilepsy is True
    assert asset.animated is True


def test_webm_thumb_marks_asset_animated_when_type_is_missing():
    client = _client(
        [
            FakeResponse(
                200,
                {
                    "success": True,
                    "data": [
                        {
                            "id": 116506,
                            "url": "https://cdn.example/a.png",
                            "thumb": "https://cdn.example/a.webm",
                            "mime": "image/png",
                        }
                    ],
                },
            )
        ]
    )
    asset = client.get_grids(1)[0]
    assert asset.animated is True
    assert preview_download_url(asset) == asset.url
    assert asset_download_url(asset) == asset.url


def test_invalid_asset_url_is_dropped():
    client = _client(
        [
            FakeResponse(
                200,
                {
                    "success": True,
                    "data": [
                        {"id": 1, "url": "javascript:alert(1)"},
                        {"id": 2, "url": "https://cdn.example/ok.png"},
                    ],
                },
            )
        ]
    )
    assets = client.get_grids(1)
    assert [asset.id for asset in assets] == [2]


def test_identical_gets_are_cached():
    client = _client(
        [
            FakeResponse(
                200,
                {"success": True, "data": [{"id": 1, "name": "Kate", "types": [], "verified": True}]},
            )
        ]
    )
    first = client.search_games("Kate")
    second = client.search_games("Kate")
    assert first == second
    assert len(client.session.calls) == 1


def test_missing_api_key_raises(tmp_path):
    environ = {"XDG_CONFIG_HOME": str(tmp_path / "config")}
    with pytest.raises(MissingAPIKeyError):
        SteamGridDBClient(environ=environ)


def test_resolve_prefers_env_over_file(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    save_api_key("from-file", environ={"XDG_CONFIG_HOME": str(tmp_path / "config")})
    resolved = resolve_api_key(
        environ={"XDG_CONFIG_HOME": str(tmp_path / "config"), ENV_KEY: "from-env"}
    )
    assert resolved == "from-env"


def test_save_api_key_is_mode_600(tmp_path):
    environ = {"XDG_CONFIG_HOME": str(tmp_path / "config")}
    path = save_api_key("stored-secret", environ=environ)
    assert path == default_key_path(environ)
    assert path.read_text(encoding="utf-8").strip() == "stored-secret"
    assert (path.stat().st_mode & 0o777) == 0o600
    clear_stored_api_key(environ=environ)
    assert not path.exists()


def test_search_queries_keep_the_original_first():
    assert search_queries("Zoom (Flatpak)") == ("Zoom (Flatpak)", "Zoom")
    assert search_queries("Kate") == ("Kate",)


def test_sniff_image_accepts_png_jpeg_gif_webp():
    assert sniff_image(PNG_1X1) == "png"
    assert sniff_image(b"\xff\xd8\xff\xe0") == "jpeg"
    assert sniff_image(b"GIF89a....") == "gif"
    assert sniff_image(WEBP_HEADER) == "webp"
    assert sniff_image(b"<!DOCTYPE html>") is None
    assert sniff_image(b"\x1aE\xdf\xa3") is None
    assert unrecognised_image_reason(b"\x1aE\xdf\xa3....") == "WebM video, not a still image"
    assert unrecognised_image_reason(b"") == "empty file"
    assert unrecognised_image_reason(b'{"code":"not found"}') == "JSON"


def test_download_png_to_temp(tmp_path):
    session = ScriptedSession(
        [FakeResponse(200, json_body=None, content=PNG_1X1, url="https://cdn.example/a.png")]
    )
    session.headers["Authorization"] = "Bearer test-secret-key"
    dest = tmp_path / "art.png"
    result = download_url("https://cdn.example/a.png", dest, session=session)
    assert result.read_bytes() == PNG_1X1
    auth = session.sent_headers[-1].get("Authorization")
    assert not auth


def test_download_rejects_html(tmp_path):
    session = ScriptedSession(
        [
            FakeResponse(
                200,
                json_body=None,
                content=b"<!DOCTYPE html><html>nope</html>",
                url="https://cdn.example/nope",
            )
        ]
    )
    dest = tmp_path / "art.png"
    with pytest.raises(InvalidResponseError, match="not a recognised image"):
        download_url("https://cdn.example/nope", dest, session=session)
    assert not dest.exists()
    assert not (tmp_path / "art.png.part").exists()


def test_download_limit_allows_large_animated_heroes():
    assert DEFAULT_MAX_BYTES >= 50 * 1024 * 1024


def test_download_rejects_declared_length_over_limit(tmp_path):
    response = FakeResponse(
        200,
        json_body=None,
        content=PNG_1X1,
        headers={"Content-Length": str(200 * 1024 * 1024)},
        url="https://cdn.example/huge.png",
    )
    session = ScriptedSession([response])
    dest = tmp_path / "art.png"
    with pytest.raises(InvalidResponseError, match="limit 100 MB"):
        download_url("https://cdn.example/huge.png", dest, session=session)
    assert not dest.exists()
    assert response.closed is True


def test_download_aborts_when_body_exceeds_limit(tmp_path):
    session = ScriptedSession(
        [FakeResponse(200, json_body=None, content=b"x" * 80, url="https://cdn.example/a")]
    )
    dest = tmp_path / "art.png"
    with pytest.raises(InvalidResponseError, match="exceeded"):
        download_url("https://cdn.example/a", dest, session=session, max_bytes=50)
    assert not dest.exists()


def test_ico_assets_download_the_png_thumb(tmp_path):
    asset = GridAsset(
        id=8319,
        kind="icon",
        url="https://cdn.example/icon.ico",
        thumb="https://cdn.example/256x256.png",
        mime="image/vnd.microsoft.icon",
    )
    assert asset_download_url(asset) == asset.thumb
    session = ScriptedSession(
        [FakeResponse(200, json_body=None, content=PNG_1X1, url=asset.thumb)]
    )
    client = SteamGridDBClient(
        "test-secret-key", session=session, sleeper=lambda _delay: None
    )
    dest = tmp_path / "icon"
    client.download_asset(asset, dest)
    assert dest.read_bytes() == PNG_1X1
    assert "256x256.png" in session.calls[-1][1]
    assert not session.calls[-1][1].endswith(".ico")


def test_grid_asset_positional_layout_keeps_lock_and_author_name():
    asset = GridAsset(
        1,
        "grid",
        "https://cdn.example/grid.png",
        "",
        "",
        0,
        None,
        None,
        "",
        (),
        "",
        "",
        False,
        False,
        False,
        True,
        "alice",
    )
    assert asset.lock is True
    assert asset.author_name == "alice"
    assert asset.animated is False


def test_png_icons_still_download_the_full_url():
    asset = GridAsset(
        id=1,
        kind="icon",
        url="https://cdn.example/icon.png",
        thumb="https://cdn.example/thumb.png",
        mime="image/png",
    )
    assert asset_download_url(asset) == asset.url
    assert preview_download_url(asset) == asset.thumb


def test_preview_download_skips_webm_thumbs(tmp_path):
    asset = GridAsset(
        id=116506,
        kind="grid",
        url="https://cdn.example/grid.png",
        thumb="https://cdn.example/thumb.webm",
        mime="image/png",
    )
    assert preview_download_url(asset) == asset.url
    session = ScriptedSession(
        [FakeResponse(200, json_body=None, content=PNG_1X1, url=asset.url)]
    )
    client = SteamGridDBClient(
        "test-secret-key", session=session, sleeper=lambda _delay: None
    )
    dest = tmp_path / "preview"
    client.download_asset(asset, dest, preview=True)
    assert dest.read_bytes() == PNG_1X1
    assert session.calls[-1][1] == asset.url


def test_download_asset_uses_client_read_timeout(tmp_path):
    asset = GridAsset(
        id=2,
        kind="grid",
        url="https://cdn.example/grid.png",
    )

    class TimeoutSession(ScriptedSession):
        def __init__(self):
            super().__init__([FakeResponse(200, json_body=None, content=PNG_1X1, url=asset.url)])
            self.send_timeout = None

        def send(self, prepared, **kwargs):
            self.send_timeout = kwargs.get("timeout")
            return super().send(prepared, **kwargs)

    session = TimeoutSession()
    client = SteamGridDBClient(
        "test-secret-key",
        session=session,
        read_timeout=7.5,
        sleeper=lambda _delay: None,
    )
    dest = tmp_path / "grid.png"
    client.download_asset(asset, dest)
    assert session.send_timeout == (5.0, 7.5)


def test_download_accepts_webp_with_png_name(tmp_path):
    session = ScriptedSession(
        [FakeResponse(200, json_body=None, content=WEBP_HEADER, url="https://cdn.example/a.png")]
    )
    dest = tmp_path / "123p.png"
    download_url("https://cdn.example/a.png", dest, session=session)
    assert dest.read_bytes() == WEBP_HEADER


def test_download_timeout(tmp_path):
    session = ScriptedSession([requests.Timeout("read timed out")])
    with pytest.raises(SteamGridDBTimeoutError):
        download_url("https://cdn.example/a.png", tmp_path / "a.png", session=session)


def test_request_exception_redacts_the_key():
    session = ScriptedSession([requests.ConnectionError("proxy failed for test-secret-key")])
    client = SteamGridDBClient("test-secret-key", session=session, sleeper=lambda _delay: None, max_retries=0)
    with pytest.raises(Exception) as caught:
        client.search_games("Kate")
    assert "test-secret-key" not in str(caught.value)
