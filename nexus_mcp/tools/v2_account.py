"""Tools: v2 GraphQL account prefs, media, uploads, age verification, reports."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from .._annotations import (
    _MUTATING_ANNOTATIONS,
    _READ_ONLY_ANNOTATIONS,
)
from .._core import (
    _MOD_SEARCH_FIELDS,
    _gql_call,
    _gql_page,
    _opt,
    _split_ids,
)
from .._server import mcp


@mcp.tool(
    name="nexus_get_age_verification_info",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Get age verification info (v2)"},
)
async def nexus_get_age_verification_info(
    user_id: int | None = Field(default=None, description="User ID. Omit for the current user."),
) -> str:
    """Check a user's age verification status [v2 - no v1 quota]. Omit user_id to check the current user.

    Returns:
        JSON {verified, externalVerificationIds: [{createdAt, externalVerificationId}]}.
    """
    return await _gql_call(
        "query($u: ID) { ageVerificationInfo(userId: $u) { verified externalVerificationIds { createdAt externalVerificationId } } }",
        {"u": _opt(user_id)},
    )


@mcp.tool(
    name="nexus_get_api_applications",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "List your API applications (v2)"},
)
async def nexus_get_api_applications() -> str:
    """List the API applications registered to your account, including API keys [v2 - no v1 quota]. Returns your account's own data only.

    Returns:
        JSON list of {active, id, key, name, slug, summary}.
    """
    return await _gql_call("query { applications { active id key name slug summary } }")


@mcp.tool(
    name="nexus_get_category_by_id",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Get a collection category (v2)"},
)
async def nexus_get_category_by_id(
    category_id: int = Field(..., description="Category ID.", ge=1),
) -> str:
    """Get a collection category by its ID [v2 - no v1 quota].

    Returns:
        JSON {id, name, description, parentId, approved, createdAt, updatedAt}.
    """
    return await _gql_call(
        "query($id: ID!) { category(id: $id) { id name description parentId approved createdAt updatedAt discardedAt } }",
        {"id": str(category_id)},
    )


@mcp.tool(
    name="nexus_get_collection_games",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "List collection-supported games (v2)"},
)
async def nexus_get_collection_games() -> str:
    """List games that support collections [v2 - no v1 quota].

    Returns:
        JSON list of {id, name, domainName, modCount, collectionCount}.
    """
    return await _gql_call(
        "query { collectionGames { id name domainName modCount collectionCount } }"
    )


@mcp.tool(
    name="nexus_get_current_warnings",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Get your warnings/notices (v2)"},
)
async def nexus_get_current_warnings() -> str:
    """Get your unread moderation warnings and global notices [v2 - no v1 quota].

    Returns:
        JSON {unreadWarnings: [{id, category, date, isRead, link, publicReason, reason,
        postId, removedDate, removedReason}], unreadGlobalNotices: [{content, date}]}.
    """
    return await _gql_call(
        "query { currentWarnings { unreadWarnings { id category date isRead link publicReason reason postId removedDate removedReason staff { memberId name } user { memberId name } } unreadGlobalNotices { content date staff { memberId name } } } }"
    )


@mcp.tool(
    name="nexus_get_external_video",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Resolve an external video (v2)"},
)
async def nexus_get_external_video(
    url: str = Field(..., description="External video URL (YouTube etc.)."),
) -> str:
    """Resolve an external video URL to embed metadata [v2 - no v1 quota].

    Returns:
        JSON {id, title, platform, embedUrl, thumbnailUrl}.
    """
    return await _gql_call(
        "query($url: String!) { externalVideo(url: $url) { id title platform embedUrl thumbnailUrl } }",
        {"url": url},
    )


@mcp.tool(
    name="nexus_get_file_hash",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Look up a file by MD5 (v2)"},
)
async def nexus_get_file_hash(
    md5: str = Field(..., description="MD5 hash of the file."),
) -> str:
    """Find which mod file matches a single MD5 hash [v2 - no v1 quota].

    Returns:
        JSON list of {md5, fileName, fileType, fileSize, gameId, modFileId, createdAt}.
    """
    return await _gql_call(
        "query($m: String!) { fileHash(md5: $m) { md5 fileName fileType fileSize gameId modFileId createdAt } }",
        {"m": md5.lower()},
    )


@mcp.tool(
    name="nexus_get_file_hashes",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Look up files by MD5 batch (v2)"},
)
async def nexus_get_file_hashes(
    md5s: str = Field(..., description="Comma-separated MD5 hashes."),
) -> str:
    """Find which mod files match a batch of MD5 hashes [v2 - no v1 quota].

    Returns:
        JSON list of {md5, fileName, fileType, fileSize, gameId, modFileId, createdAt}.
    """
    return await _gql_call(
        "query($m: [String!]!) { fileHashes(md5s: $m) { md5 fileName fileType fileSize gameId modFileId createdAt } }",
        {"m": [h.lower() for h in _split_ids(md5s)]},
    )


@mcp.tool(
    name="nexus_get_game_artwork",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Get game artwork URLs (v2)"},
)
async def nexus_get_game_artwork() -> str:
    """Get the game artwork schema URLs [v2 - no v1 quota].

    Returns:
        JSON {schemaV1: {tile, tileBlurred}, schemaV2: {hero, thumbnail, tile}}.
    """
    return await _gql_call(
        "query { gameArtwork { schemaV1 { tile tileBlurred } schemaV2 { hero thumbnail tile } } }"
    )


@mcp.tool(
    name="nexus_get_legacy_mods",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Get legacy mods by gameId:modId (v2)"},
)
async def nexus_get_legacy_mods(
    ids: str = Field(..., description='Comma-separated "gameId:modId" pairs, e.g. "1704:12604,1303:27251".'),
    offset: int = Field(default=0, description="Offset-based pagination start.", ge=0),
    count: int = Field(default=20, description="Results per page.", ge=1, le=100),
) -> str:
    """Get mods by gameId:modId pairs via the legacy bridge [v2 - no v1 quota].

    Returns:
        JSON {totalCount, _returned, nodes: [mod objects]}.
        Paginate with offset += _returned.
    """
    parsed: list[dict[str, Any]] = []
    for pair in _split_ids(ids):
        game_id, _, mod_id = pair.partition(":")
        if not game_id.isdigit() or not mod_id.isdigit():
            return "Error: ids must be comma-separated gameId:modId pairs."
        parsed.append({"gameId": int(game_id), "modId": int(mod_id)})
    if not parsed:
        return "Error: no ids provided."
    return _gql_page(
        await _gql_call(
            "query($ids: [CompositeIdInput!]!, $offset: Int, $count: Int) { legacyMods(ids: $ids, offset: $offset, count: $count) { totalCount nodesCount nodes { "
            + _MOD_SEARCH_FIELDS
            + "} } }",
            {"ids": parsed, "offset": offset, "count": count},
        ),
        "legacyMods",
    )


@mcp.tool(
    name="nexus_get_tags_v2",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "List v2 tags (v2)"},
)
async def nexus_get_tags_v2(
    game_id: int | None = Field(default=None, description="Filter by game ID."),
    category_id: int | None = Field(default=None, description="Filter by tag category ID."),
    include_global: bool | None = Field(default=None, description="Include global tags."),
    include_discarded: bool | None = Field(default=None, description="Include discarded tags."),
) -> str:
    """List v2 tags, optionally filtered [v2 - no v1 quota].

    Returns:
        JSON list of {id, name, adult, global, taggablesCount, category, games}.
    """
    return await _gql_call(
        "query($g: Int, $c: Int, $ig: Boolean, $id: Boolean) { tags(gameId: $g, categoryId: $c, includeGlobal: $ig, includeDiscarded: $id) { id name adult global taggablesCount createdAt updatedAt category { id name } games { id name domainName } } }",
        {"g": _opt(game_id), "c": _opt(category_id), "ig": _opt(include_global), "id": _opt(include_discarded)},
    )


@mcp.tool(
    name="nexus_get_tag_categories",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "List tag categories (v2)"},
)
async def nexus_get_tag_categories() -> str:
    """List all tag categories with their tags [v2 - no v1 quota].

    Returns:
        JSON list of {id, name, tags: [{id, name, adult}]}.
    """
    return await _gql_call(
        "query { tagCategories { id name createdAt updatedAt tags { id name adult } } }"
    )


@mcp.tool(
    name="nexus_get_tag_by_id",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Get a tag (v2)"},
)
async def nexus_get_tag_by_id(
    tag_id: int = Field(..., description="Tag ID.", ge=1),
) -> str:
    """Get a single tag by its ID [v2 - no v1 quota].

    Returns:
        JSON {id, name, adult, global, taggablesCount, category, games}.
    """
    return await _gql_call(
        "query($id: ID!) { tag(id: $id) { id name adult global taggablesCount createdAt updatedAt discardedAt category { id name } games { id name domainName } } }",
        {"id": str(tag_id)},
    )


@mcp.tool(
    name="nexus_get_tag_category_by_id",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Get a tag category (v2)"},
)
async def nexus_get_tag_category_by_id(
    category_id: int = Field(..., description="Tag category ID.", ge=1),
) -> str:
    """Get a single tag category by its ID [v2 - no v1 quota].

    Returns:
        JSON {id, name, tags: [{id, name, adult}]}.
    """
    return await _gql_call(
        "query($id: ID!) { tagCategory(id: $id) { id name createdAt updatedAt tags { id name adult } } }",
        {"id": str(category_id)},
    )


@mcp.tool(
    name="nexus_search_media",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Search media (images/videos) (v2)"},
)
async def nexus_search_media(
    general_search: str | None = Field(default=None, description="Free-text search."),
    game_id: int | None = Field(default=None, description="Filter by game ID."),
    game_name: str | None = Field(default=None, description="Filter by game name."),
    owner: str | None = Field(default=None, description="Filter by owner."),
    media_type: Literal["image", "video"] | None = Field(default=None, description="Filter by media type."),
    sort: Literal["newest", "oldest", "rating", "views", "random"] | None = Field(default=None, description="Sort order: newest, oldest, rating, views, or random."),
    random_seed: int | None = Field(default=None, description="Seed for random sort (used only when sort=random)."),
    view_user_blocked_content: bool | None = Field(default=None, description="Include content from blocked users."),
    offset: int = Field(default=0, description="Offset-based pagination start.", ge=0),
    count: int = Field(default=20, description="Results per page.", ge=1, le=100),
) -> str:
    """Search site-wide media (images, supporter images, videos) [v2 - no v1 quota]. This endpoint can return server-side errors; retry on failure.

    NOTE: this endpoint is SERVER-SIDE FLAKY - it intermittently fails
    with GraphQL "A name ... was not found" errors regardless of filter
    combination. Identical calls often succeed on retry; just retry.
    (An adultContent filter was deliberately removed: that filter
    consistently errors server-side for both True and False.)

    Returns:
        JSON {totalCount, _returned, nodes: [...]}. Nodes are a union (Image,
        SupporterImage, Video) discriminated by __typename.
        Paginate with offset += _returned.
    """
    flt: dict[str, Any] = {}
    if _opt(general_search) is not None:
        flt["generalSearch"] = [{"value": general_search}]
    if _opt(game_id) is not None:
        flt["gameId"] = [{"value": str(game_id)}]
    if _opt(game_name) is not None:
        flt["gameName"] = [{"value": game_name}]
    if _opt(owner) is not None:
        flt["owner"] = [{"value": owner}]
    if _opt(media_type) is not None:
        flt["type"] = [{"value": media_type}]
    sort_arg: list[dict[str, Any]] | None = None
    resolved_sort = _opt(sort)
    if resolved_sort == "newest":
        sort_arg = [{"createdAt": {"direction": "DESC"}}]
    elif resolved_sort == "oldest":
        sort_arg = [{"createdAt": {"direction": "ASC"}}]
    elif resolved_sort == "rating":
        sort_arg = [{"rating": {"direction": "DESC"}}]
    elif resolved_sort == "views":
        sort_arg = [{"views": {"direction": "DESC"}}]
    elif resolved_sort == "random":
        rand: dict[str, Any] = {}
        if _opt(random_seed) is not None:
            rand["seed"] = int(random_seed)
        sort_arg = [{"random": rand}]
    variables: dict[str, Any] = {
        "filter": flt or None,
        "sort": sort_arg,
        "offset": offset,
        "count": count,
        "vub": _opt(view_user_blocked_content),
    }
    return _gql_page(
        await _gql_call(
            """query MediaSearch($filter: MediaSearchFilter, $sort: [MediaSearchSort!], $offset: Int, $count: Int, $vub: Boolean) {
  media(filter: $filter, sort: $sort, offset: $offset, count: $count, viewUserBlockedContent: $vub) {
    totalCount nodesCount
    nodes {
      __typename
      ... on Image { id name title caption description url thumbnailUrl siteUrl adult rating views allowComments allowRating createdAt mediaStatus owner { memberId name } game { id name domainName } }
      ... on SupporterImage { id name title caption description url thumbnailUrl siteUrl rating views allowComments allowRating createdAt mediaStatus owner { memberId name } game { id name domainName } }
      ... on Video { id title description link thumbnailUrl siteUrl rating views allowComments allowRating createdAt mediaStatus owner { memberId name } game { id name domainName } }
    }
  }
}""",
            variables,
        ),
        "media",
    )


@mcp.tool(
    name="nexus_get_opted_in_mods",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "List a user's DP-opted-in mods (v2)"},
)
async def nexus_get_opted_in_mods(
    account_id: int = Field(..., description="Account ID.", ge=1),
) -> str:
    """List a user's mods that opted into Donation Points [v2 - no v1 quota].

    Returns:
        JSON {count, userId, user, entries: [{id, gameId, modId, uploaderId, ratio, createdAt}]}.
    """
    return await _gql_call(
        "query($a: Int!) { optedInMods(accountId: $a) { count userId user { memberId name } entries { id gameId modId uploaderId ratio createdAt } } }",
        {"a": account_id},
    )


@mcp.tool(
    name="nexus_get_preferences",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Get your preferences (v2)"},
)
async def nexus_get_preferences() -> str:
    """Get your site preferences (adult content, tabs, sorting, download location) [v2 - no v1 quota].

    Returns:
        JSON with adult, default tabs/sort/search, download location, reminders,
        notification and subfeed booleans. Edit with nexus_update_preferences.
    """
    return await _gql_call(
        "query { preferences { adult adultBlurImages bubbleReply comments defaultMediaTab defaultMediaTabTimeRange defaultModsTab defaultModsTabTimeRange defaultOrder defaultSearchType defaultSearchView disableProfileActivity displayLastActivity dlLocation download imageShowcase isBlockingContent marketingEmails notificationsActive notificationsGameSpecific reminder results subfeedsActivityTracked subfeedsActivityYour subfeedsAuthorTracked subfeedsCommentsTracked subfeedsCommentsYour } }"
    )


@mcp.tool(
    name="nexus_get_private_message_url",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Get private message URL (v2)"},
)
async def nexus_get_private_message_url(
    message_id: int = Field(..., description="Private message ID.", ge=1),
) -> str:
    """Get the web URL for one of your private messages [v2 - no v1 quota].

    Returns:
        JSON string URL or an error string.
    """
    return await _gql_call(
        "query($id: ID!) { privateMessageUrl(id: $id) }",
        {"id": str(message_id)},
    )


@mcp.tool(
    name="nexus_get_transactions",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Get DP transactions (v2)"},
)
async def nexus_get_transactions(
    start: int | None = Field(default=None, description="Pagination start.", ge=0),
    per_page: int | None = Field(default=None, description="Results per page.", ge=1, le=100),
    order_dir: str | None = Field(default=None, description="Order direction."),
    order_column: str | None = Field(default=None, description="Order column."),
    account_id: int | None = Field(default=None, description="Filter by account ID."),
    bank_id: int | None = Field(default=None, description="Filter by bank ID."),
    search: str | None = Field(default=None, description="Search string."),
) -> str:
    """Get your Donation Points transactions [v2 - no v1 quota] [OAuth required]. Nexus hides the data under API-key auth; you must authenticate via nexus_oauth_login + nexus_oauth_exchange, or the query returns a permission error.

    Returns:
        JSON {totalCount, filteredCount, transactions: [{id, type, label, amount,
        createdAt, creditorEntity, debitorEntity}]}.
    """
    return await _gql_call(
        "query($s: Int, $p: Int, $od: String, $oc: String, $a: Int, $b: Int, $q: String) { transactions(start: $s, perPage: $p, orderDir: $od, orderColumn: $oc, accountId: $a, bankId: $b, search: $q) { totalCount filteredCount transactions { id type label amount createdAt creditorEntity { id label type } debitorEntity { id label type } } } }",
        {"s": _opt(start), "p": _opt(per_page), "od": _opt(order_dir), "oc": _opt(order_column),
         "a": _opt(account_id), "b": _opt(bank_id), "q": _opt(search)},
    )


@mcp.tool(
    name="nexus_get_uploads",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "List your uploads (v2)"},
)
async def nexus_get_uploads(
    start: int = Field(default=0, description="Pagination start.", ge=0),
    per_page: int = Field(default=20, description="Results per page.", ge=1, le=100),
    order_column: str = Field(default="createdAt", description="Order column (e.g. createdAt)."),
    order_dir: str = Field(default="DESC", description="Order direction (ASC/DESC)."),
    upload_id: str | None = Field(default=None, description="Filter by upload ID."),
    search: str | None = Field(default=None, description="Search string."),
    filter: str | None = Field(default=None, description="Filter string."),
    upload_type: str | None = Field(default=None, description="Filter by upload type."),
    game_id: int | None = Field(default=None, description="Filter by game ID."),
    user_id: int | None = Field(default=None, description="Filter by user ID."),
    file_id: int | None = Field(default=None, description="Filter by file ID."),
    mod_id: int | None = Field(default=None, description="Filter by mod ID."),
) -> str:
    """List mod file uploads with scan status [v2 - no v1 quota].

    Returns:
        JSON {totalCount, filteredCount, uploads: [{id, status, uploadType, md5,
        sha256, virusTotalStatus, ...}]}.
    """
    return await _gql_call(
        "query($s: Int!, $p: Int!, $oc: String!, $od: String!, $id: String, $q: String, $f: String, $ut: String, $g: Int, $u: Int, $fi: Int, $m: Int) { uploads(start: $s, perPage: $p, orderColumn: $oc, orderDir: $od, id: $id, search: $q, filter: $f, uploadType: $ut, gameId: $g, userId: $u, fileId: $fi, modId: $m) { totalCount filteredCount uploads { id status uploadType createdAt updatedAt tempFileName s3Url s3UploadComplete md5 sha256 sizeBytes fileId modId claimed chunksCurrent chunksTotal internalVirusScanStatus virusTotalStatus virusTotalPositives virusTotalUrl lastError processingEngine } } }",
        {"s": start, "p": per_page, "oc": order_column, "od": order_dir,
         "id": _opt(upload_id), "q": _opt(search), "f": _opt(filter), "ut": _opt(upload_type),
         "g": _opt(game_id), "u": _opt(user_id), "fi": _opt(file_id), "m": _opt(mod_id)},
    )


@mcp.tool(
    name="nexus_get_user_donation_preferences",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Get DP donation preferences (v2)"},
)
async def nexus_get_user_donation_preferences() -> str:
    """Get your Donation Points donation preferences [v2 - no v1 quota].

    Returns:
        JSON {donateStraight, donateProfile, donateAuthorpremium, donateOwnpremium,
        donatePremiumMax, paypal}. Edit with nexus_update_user_donation_preferences.
    """
    return await _gql_call(
        "query { userDonationPreferences { id donateStraight donateProfile donateAuthorpremium donateOwnpremium donatePremiumMax paypal } }"
    )


@mcp.tool(
    name="nexus_get_user_monthly_report_by_id",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Get monthly DP report by ID (v2)"},
)
async def nexus_get_user_monthly_report_by_id(
    account_id: int = Field(..., description="Account ID.", ge=1),
    report_id: int = Field(..., description="Report ID (from nexus_get_user_monthly_summary entries... use report lookup).", ge=1),
) -> str:
    """Get one monthly Donation Points report by report ID [v2 - no v1 quota]. Privacy-restricted accounts get a "hidden due to permissions" error - an API-side restriction, not a tool failure.

    Returns:
        JSON {userId, entries: [{reportId, year, month, value, status, ratio,
        authorId, authorValue, gameId, modId, modCount, modValue, authorCount}]}.
    """
    return await _gql_call(
        "query($a: Int!, $r: Int!) { userMonthlyReportById(accountId: $a, reportId: $r) { userId entries { reportId year month value status ratio authorId authorValue gameId modId modCount modValue authorCount } } }",
        {"a": account_id, "r": report_id},
    )


@mcp.tool(
    name="nexus_request_media_upload_url",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Request media upload URL (v2)"},
)
async def nexus_request_media_upload_url(
    filename: str | None = Field(default=None, description="File name including extension."),
    mime_type: str | None = Field(default=None, description="MIME type."),
) -> str:
    """Request a presigned URL for uploading media [v2 - no v1 quota]. Upload the file to the returned url, then reference the uuid in your media mutations.

    Returns:
        JSON {url, uuid}. Upload the file to url, then reference the uuid.
    """
    return await _gql_call(
        "query($f: String, $m: String) { requestMediaUploadUrl(filename: $f, mimeType: $m) { url uuid } }",
        {"f": _opt(filename), "m": _opt(mime_type)},
    )


@mcp.tool(
    name="nexus_get_collection_revision_upload_url",
    annotations={**_READ_ONLY_ANNOTATIONS, "title": "Request revision upload URL (v2)"},
)
async def nexus_get_collection_revision_upload_url() -> str:
    """Request a presigned URL for uploading a collection revision bundle [v2 - no v1 quota].

    Returns:
        JSON {url, uuid}.
    """
    return await _gql_call("query { collectionRevisionUploadUrl { url uuid } }")


@mcp.tool(
    name="nexus_start_age_verification_flow",
    annotations={**_MUTATING_ANNOTATIONS, "title": "Start age verification flow (v2)"},
)
async def nexus_start_age_verification_flow() -> str:
    """Start the age verification flow for your account [v2 - no v1 quota]. This is an ACTION, not a read: it opens a verification session you complete in the browser.

    Returns:
        JSON {success, message, verificationResult: {id, url}} - open url to verify.
    """
    return await _gql_call(
        "query { startAgeVerificationFlow { success message verificationResult { id url } } }"
    )


@mcp.tool(
    name="nexus_start_age_verification_appeal_flow",
    annotations={**_MUTATING_ANNOTATIONS, "title": "Start age verification appeal (v2)"},
)
async def nexus_start_age_verification_appeal_flow() -> str:
    """Start the age verification appeal flow for your account [v2 - no v1 quota]. This is an ACTION, not a read: it opens an appeal session you complete in the browser.

    Returns:
        JSON {success, message, verificationResult: {id, url}} - open url to continue.
    """
    return await _gql_call(
        "query { startAgeVerificationAppealFlow { success message verificationResult { id url } } }"
    )


# ---------------------------------------------------------------------------
# Tools: v2 GraphQL - account mutations
# ---------------------------------------------------------------------------
