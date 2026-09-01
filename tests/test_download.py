"""nexus_download_mod_file writes the file locally, verifies checksums, and reports overwrites."""
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
_HELLO_MD5 = hashlib.md5(b"hello").hexdigest()
_HELLO_SHA256 = hashlib.sha256(b"hello").hexdigest()
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


def _api_handler(info=None, graphql=None):
    def handler(request):
        path = request.url.path
        if "download_link.json" in path:
            return httpx.Response(200, json=_MIRRORS)
        if "/files/" in path:
            body = info if info is not None else {"file_id": 2}
            return httpx.Response(200, json=body)
        if path.endswith("/v2/graphql"):
            return httpx.Response(200, json=graphql if graphql is not None else {"data": {"fileHash": None}})
        return httpx.Response(404, json={"error": "unexpected path"})

    return handler


def _virus(info_url=None, sha256=_HELLO_SHA256):
    return {"external_virus_scan_url": f"https://www.virustotal.com/gui/file/{sha256}"}


def _download(tmp_path, **kwargs):
    kwargs.setdefault("domain_name", "skyrimse")
    kwargs.setdefault("mod_id", 1)
    kwargs.setdefault("file_id", 2)
    kwargs.setdefault("destination", str(tmp_path))
    return run(nexus_download_mod_file(**kwargs))


def _ok(raw):
    assert isinstance(raw, str) and not raw.startswith("Error:"), raw
    return json.loads(raw)


class TestDownloadModFile:
    def test_writes_file_and_verifies_against_virus_url(self, tmp_path, monkeypatch):
        install(_api_handler(info=_virus()))
        _stream(monkeypatch, [b"hello"])

        out = _ok(_download(tmp_path))

        assert out["file"] == str(tmp_path / "file.zip")
        assert out["bytes"] == 5
        assert out["md5"] == _HELLO_MD5
        assert out["sha256"] == _HELLO_SHA256
        assert out["verified"] is True
        assert "external_virus_scan_url" in out["verified_note"]
        assert out["mirror"] == "CDN"
        assert out["overwrote"] is False
        assert Path(out["file"]).read_bytes() == b"hello"

    def test_verifies_via_graphql_filehash_when_no_virus_url(self, tmp_path, monkeypatch):
        info = {"file_id": 2, "name": "file.zip", "version": "1.0"}
        graphql = {"data": {"fileHash": {"md5": _HELLO_MD5, "modFileId": 2, "fileName": "file.zip"}}}
        install(_api_handler(info=info, graphql=graphql))
        _stream(monkeypatch, [b"hello"])

        out = _ok(_download(tmp_path))

        assert out["verified"] is True
        assert "fileHash" in out["verified_note"]
        assert Path(out["file"]).read_bytes() == b"hello"

    def test_marks_unverified_when_no_expected_hash_obtainable(self, tmp_path, monkeypatch):
        install(_api_handler(info={"file_id": 2}, graphql={"data": {"fileHash": None}}))
        _stream(monkeypatch, [b"hello"])

        out = _ok(_download(tmp_path))

        assert out["verified"] is False
        assert "verified_note" in out
        assert Path(out["file"]).read_bytes() == b"hello"

    def test_reports_overwrite_of_existing_file(self, tmp_path, monkeypatch):
        (tmp_path / "file.zip").write_bytes(b"stale")
        install(_api_handler(info=_virus()))
        _stream(monkeypatch, [b"hello"])

        out = _ok(_download(tmp_path))

        assert out["overwrote"] is True
        assert Path(out["file"]).read_bytes() == b"hello"

    def test_sha256_mismatch_deletes_temp_and_keeps_existing(self, tmp_path, monkeypatch):
        (tmp_path / "file.zip").write_bytes(b"stale")
        install(_api_handler(info=_virus(sha256="0" * 64)))
        _stream(monkeypatch, [b"hello"])

        result = _download(tmp_path)

        assert result.startswith("Error:"), result
        assert "SHA-256 mismatch" in result
        assert (tmp_path / "file.zip").read_bytes() == b"stale"
        assert not [p for p in tmp_path.iterdir() if p.suffix == ".part"]

    def test_network_error_mid_stream_keeps_existing_and_no_temp(self, tmp_path, monkeypatch):
        (tmp_path / "file.zip").write_bytes(b"stale")
        install(_api_handler(info=_virus()))

        class _ErrorStream(_FakeStreamResp):
            async def aiter_bytes(self, n):
                yield b"hello"
                raise httpx.ConnectError("boom", request=httpx.Request("GET", "https://cdn.example/file.zip"))

        _RESP[0] = _ErrorStream([b"hello"])
        monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

        result = _download(tmp_path)

        assert result.startswith("Error:"), result
        assert (tmp_path / "file.zip").read_bytes() == b"stale"
        assert not [p for p in tmp_path.iterdir() if p.suffix == ".part"]

    def test_file_info_fetch_failure_falls_back_to_graphql(self, tmp_path, monkeypatch):
        def handler(request):
            if "download_link.json" in request.url.path:
                return httpx.Response(200, json=_MIRRORS)
            if "/files/" in request.url.path:
                return httpx.Response(401, json={"message": "rate limited"})
            if request.url.path.endswith("/v2/graphql"):
                return httpx.Response(200, json={"data": {"fileHash": None}})
            return httpx.Response(404, json={"error": "unexpected path"})

        install(handler)
        _stream(monkeypatch, [b"hello"])

        out = _ok(_download(tmp_path))

        assert out["verified"] is False
        assert "fileHash" in out["verified_note"]