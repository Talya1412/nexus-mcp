"""Tests for the nexusmods.com web-scraping tools: SSRF guard, URL validation, HTML->text extraction."""
import asyncio
import json

import nexus_mcp
import nexus_mcp._core as core
from nexus_mcp.tools import web_scrape
from nexus_mcp.tools.web_scrape import (
    _check_url,
    _extract_text,
    _extract_thread_ids,
    _parse_posts,
    _truncate,
)


def _call_tool(name: str, args: dict) -> str:
    """Invoke a tool through the MCP entrypoint (resolves arg-model defaults; direct fn calls get FieldInfo defaults)."""
    result = asyncio.run(nexus_mcp.mcp.call_tool(name, args))
    return result[0][0].text


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


def _node(comment_id, name, member_id, body, date_epoch, classes="", kids="", profile_slug=None):
    """Build a <li class="comment ..."> block matching the parser's contract."""
    profile = profile_slug or name
    return (
        f'<li class="comment {classes}" id="comment-{comment_id}">'
        f'<div class="comment-head clearfix">'
        f'<a class="comment-user" href="https://www.nexusmods.com/skyrimspecialedition/profile/{profile}">'
        f'<img src="https://avatars.nexusmods.com/{member_id}/100" title="{name}"/></a>'
        f'<div class="comment-details"><span class="comment-name">{name}</span></div>'
        f'</div>'
        f'<div class="comment-content">'
        f'<time class="dst-date-adjust" data-date="{date_epoch}">1 Nov 2024</time>'
        f'<div class="comment-content-text">{body}</div>'
        f'</div>'
        + (f'<ol class="comment-kids">{kids}</ol>' if kids else "")
        + "</li>"
    )


def _page_html(page=1, with_author_reply=True, thread_id=12345):
    """Fixture posts-tab page: sticky, author-started, answered, unanswered threads."""
    author_reply = _node(300, "TheAuthor", 555, "Fixed in v2", "1700000200", classes="comment-author")
    answered_kids = author_reply if with_author_reply else ""
    return (
        "<html><head><title>Test Mod</title></head><body>"
        f'<script>widget({{thread_id: {thread_id}, "page": {page}}});</script>'
        '<div class="pagination"><a onclick="Send(\'page\', 1)">1</a><a onclick="Send(\'page\', 2)">2</a></div>'
        '<ol class="comments">'
        + _node(100, "StickyStart", 100, "Pinned note", "1700000000", classes="comment-sticky")
        + _node(101, "TheAuthor", 555, "Release notes", "1700000001", classes="comment-author")
        + _node(102, "Fansie", 777, "Will this work with 1.4?", "1700000100", kids=answered_kids)
        + _node(
            103,
            "Quinn",
            888,
            "Looks great!",
            "1700000101",
            kids=_node(301, "Bystander", 999, "Me too", "1700000102"),
        )
        + _node(104, "River", 666, "Any ETA?", "1700000103")
        + "</ol></body></html>"
    )


def test_parse_posts_structure():
    parsed = _parse_posts(_page_html())
    assert parsed["thread_id"] == 12345
    assert parsed["page_info"] == {"page": 1, "total_pages": 2}

    threads = parsed["threads"]
    assert len(threads) == 5
    sticky, author_started, answered, unanswered, no_reply = threads

    assert sticky["comment_id"] == 100 and sticky["is_sticky"] is True and sticky["is_author"] is False
    assert author_started["comment_id"] == 101 and author_started["is_author"] is True
    assert answered["comment_id"] == 102 and answered["has_author_reply"] is True
    assert len(answered["replies"]) == 1
    assert answered["replies"][0]["is_author"] is True
    assert answered["replies"][0]["author"] == "TheAuthor"

    assert unanswered["comment_id"] == 103 and unanswered["has_author_reply"] is False
    assert len(unanswered["replies"]) == 1
    assert unanswered["replies"][0]["author"] == "Bystander"
    assert unanswered["author"] == "Quinn"
    assert unanswered["member_id"] == 888
    assert unanswered["profile"] == "Quinn"
    assert unanswered["timestamp"] == 1700000101
    assert unanswered["date"] == "1 Nov 2024"
    assert "Looks great!" in unanswered["body"]

    assert no_reply["comment_id"] == 104 and no_reply["has_author_reply"] is False
    assert no_reply["replies"] == []


def test_parse_posts_no_reply_flag_when_no_author_reply():
    parsed = _parse_posts(_page_html(with_author_reply=False))
    answered = parsed["threads"][2]
    assert answered["comment_id"] == 102
    assert answered["has_author_reply"] is False
    assert answered["replies"] == []


def test_parse_posts_minimal_page():
    parsed = _parse_posts("<html><body></body></html>")
    assert parsed["page_info"] == {"page": 1, "total_pages": 1}
    assert parsed["thread_id"] is None
    assert parsed["threads"] == []


def test_nexus_get_mod_posts_structured_json(monkeypatch):
    async def fake_fetch(url: str) -> str:
        return _page_html(page=3)

    monkeypatch.setattr(web_scrape, "_fetch_html", fake_fetch)
    out = _call_tool("nexus_get_mod_posts", {"domain_name": "skyrimspecialedition", "mod_id": 12604})
    data = json.loads(out)
    assert data["page_info"] == {"page": 3, "total_pages": 2}
    assert data["thread_id"] == 12345
    assert len(data["threads"]) == 5
    assert data["threads"][2]["has_author_reply"] is True


def test_nexus_get_mod_posts_fetch_error(monkeypatch):
    async def boom(url: str) -> str:
        raise core.NexusApiError("Page request failed")

    monkeypatch.setattr(web_scrape, "_fetch_html", boom)
    out = _call_tool("nexus_get_mod_posts", {"domain_name": "skyrimspecialedition", "mod_id": 12604})
    assert out.startswith("Error:")
    assert "Page request failed" in out


def test_find_unreplied_explicit_uploader_and_mod_ids(monkeypatch):
    async def fake_fetch(url: str) -> str:
        return _page_html()

    monkeypatch.setattr(web_scrape, "_fetch_html", fake_fetch)
    out = _call_tool(
        "nexus_find_unreplied_comments",
        {"domain_name": "skyrimspecialedition", "uploader_id": 555, "mod_ids": "12604, 12605", "pages": 1},
    )
    data = json.loads(out)
    assert data["uploader_id"] == 555
    assert data["scanned"] == {"mods": 2, "pages": 2}
    # Per mod: thread 103 (non-author reply, no author reply) + thread 104 (no replies)
    assert len(data["unreplied"]) == 4
    assert {entry["author"] for entry in data["unreplied"]} == {"Quinn", "River"}
    # Sticky + author-started + answered threads must never appear
    assert all(entry["comment_id"] in (103, 104) for entry in data["unreplied"])
    by_thread = {entry["comment_id"]: entry for entry in data["unreplied"]}
    assert by_thread[103]["replies_count"] == 1
    assert by_thread[104]["replies_count"] == 0
    assert "Looks great!" in by_thread[103]["body"]
    assert "hint" in data


def test_find_unreplied_non_numeric_mod_ids_ignored(monkeypatch):
    async def fake_fetch(url: str) -> str:
        return _page_html()

    monkeypatch.setattr(web_scrape, "_fetch_html", fake_fetch)
    out = _call_tool(
        "nexus_find_unreplied_comments",
        {"domain_name": "skyrimsse", "uploader_id": 555, "mod_ids": "12604, abc,  ,7x"},
    )
    data = json.loads(out)
    assert data["scanned"] == {"mods": 1, "pages": 1}
    assert all(entry["mod_id"] == 12604 for entry in data["unreplied"])


def test_find_unreplied_resolves_uploader_and_mods_via_api(monkeypatch):
    async def fake_api(method, path, **kwargs):
        return ({"user_id": 555}, {})

    async def fake_graphql(query, variables=None):
        return ({"mods": {"nodes": [{"modId": 12604, "name": "Test Mod"}]}}, {})

    async def fake_fetch(url: str) -> str:
        return _page_html()

    monkeypatch.setattr(web_scrape, "_api", fake_api)
    monkeypatch.setattr(web_scrape, "_graphql", fake_graphql)
    monkeypatch.setattr(web_scrape, "_fetch_html", fake_fetch)
    out = _call_tool("nexus_find_unreplied_comments", {"domain_name": "skyrimspecialedition"})
    data = json.loads(out)
    assert data["uploader_id"] == 555
    assert data["scanned"] == {"mods": 1, "pages": 1}
    assert len(data["unreplied"]) == 2


def test_find_unreplied_without_resolvable_uploader(monkeypatch):
    async def fake_api(method, path, **kwargs):
        return ({}, {})

    monkeypatch.setattr(web_scrape, "_api", fake_api)
    out = _call_tool("nexus_find_unreplied_comments", {"domain_name": "skyrimspecialedition"})
    assert out.startswith("Error:")
    assert "pass uploader_id or set NEXUS_API_KEY" in out


def test_find_unreplied_validate_error(monkeypatch):
    async def boom_api(method, path, **kwargs):
        raise core.NexusApiError("validate failed")

    monkeypatch.setattr(web_scrape, "_api", boom_api)
    out = _call_tool("nexus_find_unreplied_comments", {"domain_name": "skyrimse"})
    assert out.startswith("Error:")
    assert "validate failed" in out


def test_find_unreplied_graphql_error(monkeypatch):
    async def boom_gql(query, variables=None):
        raise core.NexusApiError("GraphQL failed")

    async def fake_api(method, path, **kwargs):
        return ({"user_id": 555}, {})

    monkeypatch.setattr(web_scrape, "_graphql", boom_gql)
    monkeypatch.setattr(web_scrape, "_api", fake_api)
    out = _call_tool("nexus_find_unreplied_comments", {"domain_name": "skyrimspecialedition"})
    assert out.startswith("Error:")
    assert "GraphQL failed" in out
