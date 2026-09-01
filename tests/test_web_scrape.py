"""Tests for the nexusmods.com web-scraping tools: SSRF guard, URL validation, HTML->text extraction."""
import asyncio

import nexus_mcp._core as core
from nexus_mcp.tools import web_scrape
from nexus_mcp.tools.web_scrape import (
    _check_url,
    _extract_text,
    _extract_thread_ids,
    _truncate,
)


def test_check_url_accepts_nexus_https():
    assert (
        _check_url("https://www.nexusmods.com/skyrimspecialedition/mods/12604")
        == "https://www.nexusmods.com/skyrimspecialedition/mods/12604"
    )
    assert _check_url("https://nexusmods.com/skyrimse/mods/1") == "https://nexusmods.com/skyrimse/mods/1"
    assert _check_url("https://www.nexusmods.com/") == "https://www.nexusmods.com/"


def test_check_url_rejects_non_nexus():
    for url in (
        "http://www.nexusmods.com/foo",
        "https://evil.com/",
        "https://www.nexusmods.com.evil.com/",
        "https://www.nexusmods.com@evil.com/",
        "ftp://www.nexusmods.com/",
        "https://api.nexusmods.com/v1/games.json",
        "",
        "  ",
        None,
    ):
        result = _check_url(url)
        assert result is not None and result.startswith("Error:"), url


def test_extract_text_strips_scripts_and_blocks():
    html = (
        "<html><head><title>T</title></head><body>"
        "<script>var x = 1</script>"
        "<p>Hello</p><div>World</div>"
        "<style>.a{}</style>"
        "</body></html>"
    )
    text = _extract_text(html)
    assert "var x" not in text
    assert ".a{}" not in text
    assert "Hello" in text
    assert "World" in text


def test_extract_thread_ids():
    raw = (
        'data-thread-id="42" thread_id = 7 '
        '<a href="/posts/99">x</a>'
    )
    assert _extract_thread_ids(raw) == [7, 42, 99]


def test_truncate_annotates_truncation():
    text = "x" * 30000
    out = _truncate(text, limit=10000)
    assert len(out) < 10050
    assert "truncated" in out
    assert _truncate("short") == "short"


def test_scrape_page_returns_text_and_error(monkeypatch):
    async def fake_fetch_ok(url: str) -> str:
        return "<title>X</title><p>Body</p>"

    monkeypatch.setattr(web_scrape, "_fetch_html", fake_fetch_ok)
    out = asyncio.run(web_scrape.nexus_scrape_page("https://www.nexusmods.com/x"))
    assert "X" in out
    assert "Body" in out
    assert '"url": "https://www.nexusmods.com/x"' in out

    async def fake_fetch_boom(url: str) -> str:
        raise core.NexusApiError("Page request failed")

    monkeypatch.setattr(web_scrape, "_fetch_html", fake_fetch_boom)
    err = asyncio.run(web_scrape.nexus_scrape_page("https://www.nexusmods.com/x"))
    assert err.startswith("Error:")
    assert "Page request failed" in err


def test_scrape_page_rejects_foreign_host():
    out = asyncio.run(web_scrape.nexus_scrape_page("https://evil.com/x"))
    assert out.startswith("Error:")
