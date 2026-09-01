"""nexus_download_mod_file writes the file locally and reports overwrites."""
import asyncio
import hashlib
import json
from pathlib import Path

import httpx

import nexus_mcp._core as core
from nexus_mcp.tools.v1_rest import nexus_download_mod_file


def run(coro):
    return asyncio.run(coro)


def install(handler):
    core._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=core.API_BASE
    )


_MIRRORS = [{"URI": "https://cdn.example/file.zip", "name": "file.zip", "short_name": "CDN"}]
_RESP = [None]


class _FakeStreamResp:
    status_code = 200

    def __init__(self, chunks):
        self._chunks = chunks

    async def aiter_bytes(self, n):
        for chunk in self._chunks:
            yield chunk


class _FakeStream:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *exc):
        return False


class _FakeAsyncClient:
    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url):
        return _FakeStream(_RESP[0])


def _stream(monkeypatch, chunks):
    _RESP[0] = _FakeStreamResp(chunks)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)


def _api_handler():
    def handler(request):
        return httpx.Response(200, json=_MIRRORS)

    return handler


def _download(tmp_path):
    return json.loads(
        run(
            nexus_download_mod_file(
                domain_name="skyrimse",
                mod_id=1,
                file_id=2,
                destination=str(tmp_path),
            )
        )
    )


class TestDownloadModFile:
    def test_writes_file_and_reports_fresh_download(self, tmp_path, monkeypatch):
        install(_api_handler())
        _stream(monkeypatch, [b"hello"])

        out = _download(tmp_path)

        assert out["file"] == str(tmp_path / "file.zip")
        assert out["bytes"] == 5
        assert out["md5"] == hashlib.md5(b"hello").hexdigest()
        assert out["mirror"] == "CDN"
        assert out["overwrote"] is False
        assert Path(out["file"]).read_bytes() == b"hello"

    def test_reports_overwrite_of_existing_file(self, tmp_path, monkeypatch):
        (tmp_path / "file.zip").write_bytes(b"stale")
        install(_api_handler())
        _stream(monkeypatch, [b"hello"])

        out = _download(tmp_path)

        assert out["overwrote"] is True
        assert Path(out["file"]).read_bytes() == b"hello"
