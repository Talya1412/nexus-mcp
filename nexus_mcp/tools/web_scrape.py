"""Tools: scrapes of public nexusmods.com pages (mod page, posts tab, generic page).

These work over plain HTTPS with a browser-like User-Agent and do NOT send an
API key, so they never consume the v1 REST or v2 GraphQL quota. Only URLs under
https://www.nexusmods.com/ are allowed (SSRF guard). The v2 GraphQL comment
search endpoint is currently broken upstream, so scraping the posts tab is the
fallback way to read a mod's comment thread.
"""

from __future__ import annotations

import html.parser
import json
import re

import httpx
from pydantic import Field

from .._annotations import _READ_ONLY_ANNOTATIONS
from .._core import NexusApiError
from .._server import mcp

_MAX_TEXT_CHARS = 20000
_HTTP_TIMEOUT = 30.0
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
_NEXUS_URL_RE = re.compile(r"^https://(www\.)?nexusmods\.com(/|$)")
_SEARCH_TEXT_MAX = 8000  # readable window for the mod-page helper below

_NEXUS_URL_DESC = (
    "https:// URL of a page on https://www.nexusmods.com (e.g. "
    "'https://www.nexusmods.com/skyrimspecialedition/mods/12604'); other hosts are rejected (SSRF guard)."
)


def _check_url(url: str | None) -> str | None:
    """Convert a user-supplied URL into a validated https nexusmods.com URL, or return an error string."""
    if url is None or not url.strip():
        return "Error: url is required - pass an https://www.nexusmods.com/... page URL."
    url = url.strip()
    if not _NEXUS_URL_RE.match(url):
        return (
            "Error: url must start with https://www.nexusmods.com/ (only nexusmods.com pages can be scraped; "
            "other hosts, http:// URLs, and userinfo/ports are rejected as an SSRF guard)."
        )
    return url


class _TextExtractor(html.parser.HTMLParser):
    """HTML -> plain text: drops script/style/head, newlines around block tags."""

    _SKIP_TAGS: tuple[str, ...] = ("script", "style", "noscript", "iframe", "head", "template")

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        elif tag in ("br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article"):
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        lines = [line.strip() for line in raw.splitlines()]
        lines = [line for line in lines if line]
        return "\n".join(lines)


def _extract_text(raw: str) -> str:
    parser = _TextExtractor()
    parser.feed(raw)
    return parser.text()


def _truncate(text: str, limit: int = _MAX_TEXT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... (truncated, full page is {len(text)} chars)"


def _dump_json(body: dict) -> str:
    """Dump a result body as compact sorted JSON, skipping unset/None fields."""
    return json.dumps(
        {k: v for k, v in sorted(body.items()) if v is not None},
        indent=2,
        ensure_ascii=False,
    )


def _meta(raw: str, name: str) -> str | None:
    # <meta name="description" content="..."> and <meta property="og:title" content="...">
    # Case-insensitive attribute matching, but preserve original content case.
    for m in re.finditer(r'<meta[^>]*>', raw, re.IGNORECASE):
        tag = m.group(0)
        if re.search(rf'\b(?:name|property)\s*=\s*["\']{re.escape(name)}["\']', tag, re.IGNORECASE):
            cm = re.search(r'content\s*=\s*["\']([^"\']+)["\']', tag, re.IGNORECASE)
            if cm:
                return re.sub(r"\s+", " ", cm.group(1)).strip()
    return None


def _title(raw: str) -> str | None:
    for name in ("og:title", "twitter:title"):
        val = _meta(raw, name)
        if val:
            return val
    m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else None


async def _fetch_html(url: str) -> str:
    async with httpx.AsyncClient(
        timeout=_HTTP_TIMEOUT,
        follow_redirects=True,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    ) as hc:
        try:
            response = await hc.get(url)
        except httpx.TimeoutException as exc:
            raise NexusApiError(f"Page request to {url} timed out after {_HTTP_TIMEOUT:g}s. Try again.") from exc
        except httpx.HTTPError as exc:
            raise NexusApiError(f"Network error fetching {url}: {type(exc).__name__}: {exc}") from exc
    if response.status_code >= 400:
        hint = "Page returned 403 - Nexus may be blocking automated fetches; retry or use the API tools instead."
        raise NexusApiError(f"Page returned HTTP {response.status_code}. {hint}", status=response.status_code)
    return response.text


async def _scrape(url: str) -> str:
    validated = _check_url(url)
    if validated.startswith("Error:"):
        return validated
    try:
        raw = await _fetch_html(validated)
    except NexusApiError as exc:
        return f"Error: {exc}"
    text = _truncate(_extract_text(raw))
    return _dump_json(
        {
            "url": validated,
            "title": _title(raw),
            "description": _meta(raw, "description"),
            "text": text,
        }
    )


@mcp.tool(
    name="nexus_scrape_page",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Read a nexusmods.com page as text (web)"},
)
async def nexus_scrape_page(
    url: str = Field(..., description=_NEXUS_URL_DESC),
) -> str:
    """Read any public nexusmods.com page as simplified text via web scraping. [web - no API key, no v1/v2 quota]

    Use when there is no API tool for the page you need (e.g. an article, a
    mod's description quirks, a search results page). Only https://www.nexusmods.com/*
    URLs are allowed (SSRF guard). Returns the page title, meta description, and
    cleaned page text; blocks like scripts/styles are dropped and text is truncated.

    Returns:
        JSON {url, title?, description?, text} or an error string.
    """
    return await _scrape(url)


@mcp.tool(
    name="nexus_scrape_mod_page",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Scrape a mod page for details (web)"},
)
async def nexus_scrape_mod_page(
    domain_name: str = Field(..., description="Nexus Mods game domain (lowercase URL slug), e.g. 'forzahorizon6', 'skyrimse'. NOT the display name."),
    mod_id: int = Field(..., description="Numeric mod ID from the mod page URL, e.g. 12604.", ge=1),
) -> str:
    """Scrape a mod's page on nexusmods.com for title, version, author, and stats as plain text. [web - no API key, no v1/v2 quota]

    Complement to nexus_get_mod / nexus_get_mod_v2: useful when those cannot be
    called or you want the description text exactly as rendered. Unlike the API
    tools it sends no API key and consumes no quota.

    Returns:
        JSON {url, title, author?, version?, stats?, text} or an error string.
    """
    validated = _check_url(f"https://www.nexusmods.com/{domain_name}/mods/{mod_id}")
    if validated.startswith("Error:"):
        return validated
    try:
        raw = await _fetch_html(validated)
    except NexusApiError as exc:
        return f"Error: {exc}"
    text = _extract_text(raw)
    author = _field(text, "Author")
    version = re.search(r"\bVersion\s*:?\s*([0-9][0-9A-Za-z._\-]*)", text)
    stats = _field_block(text, "Statistics") or _field(text, "Endorsements")
    body = {
        "url": validated,
        "title": _title(raw),
        "author": author,
        "version": version.group(1) if version else None,
        "stats": stats,
        "text": _truncate(text, _SEARCH_TEXT_MAX + 12000),
    }
    return _dump_json(body)


@mcp.tool(
    name="nexus_scrape_mod_posts",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Scrape a mod's comment/posts tab (web)"},
)
async def nexus_scrape_mod_posts(
    domain_name: str = Field(..., description="Nexus Mods game domain (lowercase URL slug), e.g. 'forzahorizon6', 'skyrimse'. NOT the display name."),
    mod_id: int = Field(..., description="Numeric mod ID whose posts tab to read, e.g. 12604.", ge=1),
) -> str:
    """Scrape a mod's 'Posts' tab on nexusmods.com to read its comment threads as plain text. [web - no API key, no v1/v2 quota]

    Fallback for reading comments: the v2 searchComments endpoint is currently
    broken upstream (HTTP 500) and post-thread IDs scraped here do NOT resolve
    through the GraphQL comment tools - use this for READING the threads only.

    Returns:
        JSON {url, title?, thread_ids, text} or an error string.
    """
    validated = _check_url(f"https://www.nexusmods.com/{domain_name}/mods/{mod_id}?tab=posts")
    if validated.startswith("Error:"):
        return validated
    try:
        raw = await _fetch_html(validated)
    except NexusApiError as exc:
        return f"Error: {exc}"
    thread_ids = _extract_thread_ids(raw)
    body = {
        "url": validated,
        "title": _title(raw),
        "thread_ids": thread_ids,
        "text": _truncate(_extract_text(raw), _SEARCH_TEXT_MAX),
    }
    return _dump_json(body)


def _field(text: str, label: str) -> str | None:
    m = re.search(rf"(?:^|\n)\s*{re.escape(label)}\s*:?\s*([^\n]+)", text)
    return m.group(1).strip() if m else None


def _field_block(text: str, label: str) -> str | None:
    m = re.search(rf"(?:^|\n)\s*{re.escape(label)}\s*:?\s*\n((?:[^\n]*\n?){{0,6}})", text)
    return m.group(1).strip() if m and m.group(1).strip() else None


def _extract_thread_ids(raw: str) -> list[int]:
    ids: set[int] = set()
    for m in re.finditer(r'(?:data-thread-id|thread_id)["\']?\s*[:=]\s*["\']?(\d+)', raw):
        ids.add(int(m.group(1)))
    for m in re.finditer(r"/posts/(\d+)", raw):
        ids.add(int(m.group(1)))
    return sorted(ids)