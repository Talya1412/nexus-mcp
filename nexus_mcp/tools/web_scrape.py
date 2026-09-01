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
from .._core import (
    DOMAIN_DESC,
    NexusApiError,
    _api,
    _check_domain,
    _graphql,
)
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


# ---------------------------------------------------------------------------
# Posts-tab parsing (#5): structured comment threads with author-reply flags
# ---------------------------------------------------------------------------


class _PostsParser(html.parser.HTMLParser):
    """Parse a mod's posts-tab HTML into a tree of comment threads.

    Recognised structure (as served by nexusmods.com):
      <li class="comment [comment-sticky] [comment-author]" id="comment-<id>">
        <div class="comment-head clearfix">
          <a class="comment-user" href=".../profile/<slug>">
            <img src="https://avatars.nexusmods.com/<memberId>/100" title="<name>"/>
          </a>
          <div class="comment-details"><span class="comment-name">NAME</span>...</div>
        </div>
        <div class="comment-content">
          <time class="dst-date-adjust" data-date="<epoch>">...</time>
          <div class="comment-content-text">BODY</div>
        </div>
        <ol class="comment-kids">   <!-- replies; may nest -->
          <li class="comment comment-author" id="comment-<id>">...</li>
        </ol>
      </li>

    Classes: "comment-sticky" = pinned thread, "comment-author" = reply made by
    the mod's author (the owning member) - the basis of the unreplied scan.
    """

    _SKIP_TAGS: tuple[str, ...] = ("script", "style", "noscript", "iframe", "head", "template")

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.threads: list[dict] = []           # root-level threads
        self._stack: list[dict] = []            # open comment nodes (nesting by depth)
        self._kids_depth = 0                    # >0 while inside <ol class="comment-kids">
        self._in_comment_name = False
        self._in_time = False
        self._in_body = False
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if tag == "li" and "comment" in classes:
            node_id = None
            cid = a.get("id") or ""
            if cid.startswith("comment-") and cid[8:].isdigit():
                node_id = int(cid[8:])
            node = {
                "comment_id": node_id,
                "is_sticky": "comment-sticky" in classes,
                "is_author": "comment-author" in classes,
                "author": None,
                "member_id": None,
                "profile": None,
                "timestamp": None,
                "date": None,
                "body": "",
                "replies": [],
            }
            if self._stack and self._kids_depth:
                self._stack[-1]["replies"].append(node)
            else:
                self.threads.append(node)
            self._stack.append(node)
        elif tag == "ol" and "comment-kids" in classes:
            self._kids_depth += 1
        elif tag == "span" and "comment-name" in classes:
            self._in_comment_name = True
        elif tag == "time" and "dst-date-adjust" in classes:
            self._in_time = True
            ts = a.get("data-date") or ""
            if ts.isdigit() and self._stack:
                self._stack[-1]["timestamp"] = int(ts)
        elif tag == "div" and "comment-content-text" in classes:
            self._in_body = True
        elif tag == "a" and "comment-user" in classes:
            href = a.get("href") or ""
            m = re.search(r"/profile/([^/?#\"']+)", href)
            if m and self._stack:
                self._stack[-1]["profile"] = m.group(1)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "img" and self._stack:
            src = a.get("src") or ""
            m = re.search(r"avatars\.nexusmods\.com/(\d+)/", src)
            if m and self._stack[-1]["member_id"] is None:
                self._stack[-1]["member_id"] = int(m.group(1))
                self._stack[-1]["author"] = (a.get("title") or a.get("alt") or "").strip() or None

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if tag == "li":
            if self._stack:
                self._stack.pop()
        elif tag == "ol" and self._kids_depth:
            self._kids_depth -= 1
        elif tag == "span" and self._in_comment_name:
            self._in_comment_name = False
        elif tag == "time":
            self._in_time = False
        elif tag == "div" and self._in_body:
            self._in_body = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not self._stack:
            return
        node = self._stack[-1]
        if self._in_comment_name and node["author"] is None:
            name = data.strip()
            if name:
                node["author"] = name
        elif self._in_time and node["date"] is None:
            date = data.strip()
            if date:
                node["date"] = date
        elif self._in_body:
            node["body"] += data


def _clean_post_body(text: str) -> str:
    """Post body: collapse HTMLParser-joined text (entities already converted)."""
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def _mark_replied(node: dict) -> bool:
    """True when node (or any descendant reply) is authored by the mod author."""
    if node.get("is_author"):
        return True
    return any(_mark_replied(r) for r in node.get("replies") or [])


def _parse_posts(raw: str) -> dict:
    """Parse posts-tab HTML into {page, thread_id, total_pages, threads}.

    Each thread is {comment_id, is_sticky, is_author, author, member_id,
    profile, timestamp, date, body, replies: [...], has_author_reply}.
    has_author_reply is True when the thread or any nested reply was made by
    the mod's author - the signal 'answered by the mod author'.
    """
    parser = _PostsParser()
    parser.feed(raw)
    threads = []
    for root in parser.threads:
        has_author_reply = _mark_replied(root)
        threads.append({**root, "has_author_reply": has_author_reply})
    return {
        "page_info": _extract_page_info(raw),
        "thread_id": _extract_widget_thread_id(raw),
        "threads": threads,
    }


def _extract_page_info(raw: str) -> dict:
    """Pagination from the posts widget: {page, page_size?, total_pages}.

    The widget renders 'Send('page', N)' onclick handlers; the highest N is the
    last page ('next' re-uses the current+1, so dedupe), and 'page' itself comes
    from RH.out_items' page_size/page when present.
    """
    pages: list[int] = []
    for m in re.finditer(r"Send\('page',\s*'?(\d+)'?\)", raw):
        pages.append(int(m.group(1)))
    total_pages = max(pages) if pages else 1
    page = 1
    m = re.search(r'"page":\s*(\d+)', raw)
    if m:
        page = int(m.group(1))
    return {"page": page, "total_pages": total_pages}


def _extract_widget_thread_id(raw: str) -> int | None:
    for m in re.finditer(r"thread_id[=:]\s*[\"']?(\d+)", raw):
        return int(m.group(1))
    return None


@mcp.tool(
    name="nexus_get_mod_posts",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Read a mod's post threads, structured (web)"},
)
async def nexus_get_mod_posts(
    domain_name: str = Field(..., description=DOMAIN_DESC),
    mod_id: int = Field(..., description="Numeric mod ID whose posts tab to read, e.g. 12604.", ge=1),
    page: int = Field(default=1, description="Posts page to read (1-based). Use the returned page_info.total_pages to page through.", ge=1),
) -> str:
    """Read a mod's 'Posts' tab as structured comment threads with author-reply flags. [web - no API key, no v1/v2 quota]

    Parses the posts-tab HTML (the v2 comment search endpoint is broken
    upstream). Each thread reports the comment id, author name/member id/profile,
    timestamp, body, whether the thread is pinned (is_sticky), whether the
    comment was made by the mod's author (is_author), and whether any reply in
    the thread came from the mod's author (has_author_reply). Replies are nested.

    Returns:
        JSON {url, title, page_info: {page, total_pages}, thread_id,
        threads: [{comment_id, is_sticky, is_author, author, member_id,
        profile, timestamp, date, body, has_author_reply, replies: [...]}]}
        or an error string.
    """
    validated = _check_url(f"https://www.nexusmods.com/{domain_name}/mods/{mod_id}?tab=posts&page={page}")
    if validated.startswith("Error:"):
        return validated
    try:
        raw = await _fetch_html(validated)
    except NexusApiError as exc:
        return f"Error: {exc}"
    parsed = _parse_posts(raw)
    body = {
        "url": validated,
        "title": _title(raw),
        "page_info": parsed["page_info"],
        "thread_id": parsed["thread_id"],
        "threads": parsed["threads"],
    }
    return _dump_json(body)


@mcp.tool(
    name="nexus_find_unreplied_comments",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Find comments the mod author never replied to (web)"},
)
async def nexus_find_unreplied_comments(
    domain_name: str = Field(..., description=DOMAIN_DESC),
    uploader_id: int | None = Field(default=None, description="Nexus Mods member ID of the mod author. Defaults to the authenticated account's user_id (validate.json).", ge=1),
    mod_ids: str | None = Field(default=None, description="Comma-separated mod IDs to scan (optional). Defaults to all mods uploaded by the author (v2 GraphQL uploaderId filter)."),
    pages: int = Field(default=1, description="How many posts pages to scan per mod.", ge=1, le=20),
    max_mods: int = Field(default=25, description="When mod_ids is empty, cap how many of the author's mods are scanned (cheapest first by modId).", ge=1, le=100),
) -> str:
    """Find comment threads on the author's mods that the mod author has NEVER replied to. [web + v2]

    Resolves the mod author (your `uploader_id`, or the authenticated account),
    lists that author's mods (v2 GraphQL `mods(filter: {uploaderId ...})`), then
    scans each mod's posts tab for root threads whose replies contain no message
    from the mod author. Pinned (is_sticky) threads and threads the author
    started themselves are skipped. Requires an API key for the uploader lookup;
    the page fetches themselves send no key and consume no quota.

    Returns:
        JSON {uploader_id, domain_name, scanned: {mods, pages}, unreplied:
        [{mod_id, mod_name, page, thread_id, comment_id, author, member_id,
        timestamp, date, body, replies_count}]} or an error string.
    """
    if err := _check_domain(domain_name):
        return err
    if uploader_id is None:
        try:
            payload, _ = await _api("GET", "/v1/users/validate.json")
            member_id = payload.get("user_id") if isinstance(payload, dict) else None
            if member_id is not None:
                uploader_id = int(member_id)
        except NexusApiError as exc:
            return f"Error: {exc}"
    if uploader_id is None:
        return "Error: could not resolve the uploader - pass uploader_id or set NEXUS_API_KEY."

    targets: list[dict] = []
    if mod_ids:
        for raw_id in mod_ids.split(","):
            mod = raw_id.strip()
            if mod.isdigit():
                targets.append({"mod_id": int(mod), "mod_name": None})
    else:
        query = """
        query UpMods($filter: ModsFilter, $count: Int) {
          mods(filter: $filter, count: $count) { nodes { modId name } }
        }
        """
        try:
            data, _ = await _graphql(
                query,
                {"filter": {"uploaderId": [{"value": str(uploader_id), "op": "EQUALS"}]}, "count": max_mods},
            )
        except NexusApiError as exc:
            return f"Error: {exc}"
        nodes = (data or {}).get("mods", {}).get("nodes") if isinstance(data, dict) else None
        if not isinstance(nodes, list):
            return "Error: uploader mods lookup returned no list - check the uploader_id."
        targets = [{"mod_id": n.get("modId"), "mod_name": n.get("name")} for n in nodes if isinstance(n, dict) and n.get("modId") is not None]

    unreplied: list[dict] = []
    scanned = {"mods": 0, "pages": 0}
    for target in targets[:max_mods] if not mod_ids else targets:
        mod_id = target["mod_id"]
        for page in range(1, pages + 1):
            url = f"https://www.nexusmods.com/{domain_name}/mods/{mod_id}?tab=posts&page={page}"
            validated = _check_url(url)
            if validated.startswith("Error:"):
                continue
            try:
                raw = await _fetch_html(validated)
            except NexusApiError as exc:
                unreplied.append({"mod_id": mod_id, "error": str(exc)})
                continue
            parsed = _parse_posts(raw)
            scanned["pages"] += 1
            for thread in parsed["threads"]:
                if thread["is_sticky"] or thread["is_author"]:
                    continue
                if thread["has_author_reply"]:
                    continue
                unreplied.append(
                    {
                        "mod_id": mod_id,
                        "mod_name": target["mod_name"],
                        "page": page,
                        "thread_id": parsed["thread_id"],
                        "comment_id": thread["comment_id"],
                        "author": thread["author"],
                        "member_id": thread["member_id"],
                        "timestamp": thread["timestamp"],
                        "date": thread["date"],
                        "body": _truncate(_clean_post_body(thread["body"]), 800),
                        "replies_count": len(thread["replies"]),
                    }
                )
        scanned["mods"] += 1

    body = {
        "uploader_id": uploader_id,
        "domain_name": domain_name,
        "scanned": scanned,
        "unreplied": unreplied,
        "hint": "Unreplied threads are root comments without any author reply. Check each mod's posts tab for context.",
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