"""Tools: v2 GraphQL writes - collections/revisions/tags lifecycle, moderation, messages, DP."""

from __future__ import annotations

from typing import Any, Literal, Optional

import uuid
import base64
import json
import time

import httpx
from pydantic import Field

from .._core import (
    API_BASE,
    GRAPHQL_PATH,
    NexusApiError,
    _auth_headers,
    _gql_call,
    _inline_args,
    _opt,
    _qlit,
    _split_ids,
)

from .._annotations import (
    _DESTRUCTIVE_ANNOTATIONS,
    _DESTRUCTIVE_IDEMPOTENT_ANNOTATIONS,
    _IDEMPOTENT_MUTATION_ANNOTATIONS,
    _MUTATING_ANNOTATIONS,
)

from .._server import mcp

@mcp.tool(name="nexus_update_about_me", annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Update your About Me (v2)"})
async def nexus_update_about_me(
    about: str = Field(..., description="New About Me text.", min_length=1),
    user_id: Optional[int] = Field(default=None, description="User ID. Omit for the current user."),
) -> str:
    """Update a user's About Me profile text via v2 GraphQL.

    Returns:
        JSON {success} or an error string.
    """
    return await _gql_call(
        "mutation($a: String!, $u: ID) { updateAboutMe(about: $a, userId: $u) { ... on UpdateAboutMeMutationPayload { success } } }",
        {"a": about, "u": _opt(user_id)},
    )


@mcp.tool(name="nexus_update_country", annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Update your country (v2)"})
async def nexus_update_country(
    country: Optional[str] = Field(default=None, description="Country name/code. Omit to clear."),
    user_id: Optional[int] = Field(default=None, description="User ID. Omit for the current user."),
) -> str:
    """Update a user's country via v2 GraphQL.

    Returns:
        JSON {success} or an error string.
    """
    return await _gql_call(
        "mutation($c: String, $u: ID) { updateCountry(country: $c, userId: $u) { ... on UpdateCountryMutationPayload { success } } }",
        {"c": _opt(country), "u": _opt(user_id)},
    )


@mcp.tool(name="nexus_update_preferences", annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Update site preferences (v2)"})
async def nexus_update_preferences(
    default_mods_tab: Optional[Literal["NEW", "TRENDING", "POPULAR", "RANDOM", "UPDATED"]] = Field(default=None, description="Default mods tab."),
    default_mods_tab_time_range: Optional[Literal["ALL_TIME", "ONE_DAY", "ONE_WEEK", "TWO_WEEKS", "FOUR_WEEKS", "ONE_YEAR"]] = Field(default=None, description="Default mods tab time range."),
    default_media_tab: Optional[Literal["NEW", "TRENDING", "POPULAR", "RANDOM"]] = Field(default=None, description="Default media tab."),
    default_media_tab_time_range: Optional[Literal["ALL_TIME", "ONE_DAY", "ONE_WEEK", "TWO_WEEKS", "FOUR_WEEKS", "ONE_YEAR"]] = Field(default=None, description="Default media tab time range."),
    default_order: Optional[Literal["BY_RECENT_FILES", "BY_ENDORSEMENTS", "BY_DOWNLOADS", "BY_UNIQUE_DOWNLOADS", "BY_LAST_UPDATED_FILE", "BY_AUTHOR_NAME", "BY_FILE_NAME", "BY_FILE_SIZE", "RANDOM_SORTING", "LAST_COMMENT"]] = Field(default=None, description="Default listing order."),
    default_search_view: Optional[Literal["STANDARD", "LIST", "COMPACT"]] = Field(default=None, description="Default search view."),
    default_search_type: Optional[Literal["POP_UP_BOX", "SEPARATE_PAGE"]] = Field(default=None, description="Default search type."),
    results: Optional[Literal["RESULTS_20", "RESULTS_40", "RESULTS_60", "RESULTS_80"]] = Field(default=None, description="Results per page."),
    comments: Optional[Literal["COMMENTS_10", "COMMENTS_20", "COMMENTS_30", "COMMENTS_40", "COMMENTS_50"]] = Field(default=None, description="Comments per page."),
    dl_location: Optional[Literal["NEXUS_CDN", "AMSTERDAM", "PRAGUE", "CHICAGO", "LOS_ANGELES", "MIAMI"]] = Field(default=None, description="Preferred download location."),
    download: Optional[Literal["ALL_CONTENT", "GAMES", "MODS", "COLLECTIONS", "IMAGES", "VIDEOS", "USERS"]] = Field(default=None, description="Download method scope."),
    reminder: Optional[Literal["NEVER", "DAYS_1", "DAYS_3", "DAYS_7", "DAYS_14", "DAYS_28"]] = Field(default=None, description="Endorsement reminder window."),
    image_showcase: Optional[Literal["NOT_SET", "CHOOSE_ON_PER_IMAGE_BASIS", "TURN_OFF_IMAGES", "TURN_ON_IMAGES"]] = Field(default=None, description="Image showcase mode."),
    adult: Optional[bool] = Field(default=None, description="Show adult content."),
    adult_blur_images: Optional[bool] = Field(default=None, description="Blur adult images."),
    bubble_reply: Optional[bool] = Field(default=None, description="Bubble reply notifications."),
    disable_profile_activity: Optional[bool] = Field(default=None, description="Disable profile activity feed."),
    display_last_activity: Optional[bool] = Field(default=None, description="Display last activity."),
    marketing_emails: Optional[bool] = Field(default=None, description="Receive marketing emails."),
    notifications_active: Optional[bool] = Field(default=None, description="Notifications enabled."),
    notifications_game_specific: Optional[bool] = Field(default=None, description="Game-specific notifications."),
    subfeeds_comments_your: Optional[bool] = Field(default=None, description="Subfeed: comments on your content."),
    subfeeds_activity_your: Optional[bool] = Field(default=None, description="Subfeed: activity on your content."),
    subfeeds_comments_tracked: Optional[bool] = Field(default=None, description="Subfeed: comments on tracked content."),
    subfeeds_activity_tracked: Optional[bool] = Field(default=None, description="Subfeed: activity on tracked content."),
    subfeeds_author_tracked: Optional[bool] = Field(default=None, description="Subfeed: tracked authors."),
) -> str:
    """Update the current user's site preferences via v2 GraphQL.

    Only the provided fields are changed; omitted fields stay as-is.
    Read current values with nexus_get_preferences.

    Returns:
        JSON {success} or an error string.
    """
    args = _inline_args(
        defaultModsTab=default_mods_tab,
        defaultModsTabTimeRange=default_mods_tab_time_range,
        defaultMediaTab=default_media_tab,
        defaultMediaTabTimeRange=default_media_tab_time_range,
        defaultOrder=default_order,
        defaultSearchView=default_search_view,
        defaultSearchType=default_search_type,
        results=results,
        comments=comments,
        dlLocation=dl_location,
        download=download,
        reminder=reminder,
        imageShowcase=image_showcase,
        adult=adult,
        adultBlurImages=adult_blur_images,
        bubbleReply=bubble_reply,
        disableProfileActivity=disable_profile_activity,
        displayLastActivity=display_last_activity,
        marketingEmails=marketing_emails,
        notificationsActive=notifications_active,
        notificationsGameSpecific=notifications_game_specific,
        subfeedsCommentsYour=subfeeds_comments_your,
        subfeedsActivityYour=subfeeds_activity_your,
        subfeedsCommentsTracked=subfeeds_comments_tracked,
        subfeedsActivityTracked=subfeeds_activity_tracked,
        subfeedsAuthorTracked=subfeeds_author_tracked,
    )
    if not args:
        return "Error: provide at least one preference to update."
    return await _gql_call(
        f"mutation {{ updatePreferences({args}) {{ ... on LegacyUpdatePreferencesMutationPayload {{ success }} }} }}"
    )


@mcp.tool(
    name="nexus_update_user_donation_preferences",
    annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Update DP donation preferences (v2)"},
)
async def nexus_update_user_donation_preferences(
    donate_straight: Optional[bool] = Field(default=None, description="Donate DP straight to authors."),
    donate_authorpremium: Optional[bool] = Field(default=None, description="Donate to author premium share."),
    donate_ownpremium: Optional[bool] = Field(default=None, description="Donate from own premium share."),
    donate_profile: Optional[bool] = Field(default=None, description="Donate from profile."),
    donate_premium_max: Optional[int] = Field(default=None, description="Max premium donation amount."),
    dp_opted_in: Optional[bool] = Field(default=None, description="Opt your mods into Donation Points."),
    paypal: Optional[str] = Field(default=None, description="PayPal address for payouts."),
) -> str:
    """Update the current user's Donation Points preferences via v2 GraphQL.

    Only the provided fields are changed; omitted fields stay as-is.

    Returns:
        JSON {success, userDonationPreferences: {...}} or an error string.
    """
    args = _inline_args(
        donateStraight=donate_straight,
        donateAuthorpremium=donate_authorpremium,
        donateOwnpremium=donate_ownpremium,
        donateProfile=donate_profile,
        donatePremiumMax=donate_premium_max,
        dpOptedIn=dp_opted_in,
        paypal=paypal,
    )
    if not args:
        return "Error: provide at least one preference to update."
    return await _gql_call(
        f"mutation {{ updateUserDonationPreferences({args}) {{ ... on UpdateUserDonationPreferencesPayload {{ success userDonationPreferences {{ donateStraight donateProfile donateAuthorpremium donateOwnpremium donatePremiumMax paypal }} }} }} }}"
    )


@mcp.tool(name="nexus_create_message", annotations={**_MUTATING_ANNOTATIONS, "title": "Send a private message (v2)"})
async def nexus_create_message(
    to: str = Field(..., description="Comma-separated recipient user IDs."),
    title: str = Field(..., description="Message title.", min_length=1),
    body: str = Field(..., description="Message body (plain text).", min_length=1),
) -> str:
    """Send a private message to one or more users via v2 GraphQL.

    Returns:
        JSON {success} or an error string.
    """
    recipients = [int(x) for x in _split_ids(to) if x.isdigit()]
    if not recipients:
        return "Error: no valid recipient user IDs."
    return await _gql_call(
        "mutation($t: [Int!]!, $ti: String!, $b: String!) { createMessage(to: $t, title: $ti, body: $b) { ... on CreateMessagePayload { success } } }",
        {"t": recipients, "ti": title, "b": body},
    )


# ---------------------------------------------------------------------------
# Tools: v2 GraphQL - collection mutations
# ---------------------------------------------------------------------------


@mcp.tool(
    name="nexus_close_collection_bug_report",
    annotations={**_DESTRUCTIVE_IDEMPOTENT_ANNOTATIONS, "title": "Close a collection bug report (v2)"},
)
async def nexus_close_collection_bug_report(
    bug_report_id: int = Field(..., description="Bug report ID.", ge=1),
    closure_reason: Literal["none", "resolved", "not_a_bug", "wont_fix"] = Field(..., description="Closure reason."),
) -> str:
    """Close a bug report on your collection via v2 GraphQL.

    Returns:
        JSON {collectionBugReport: {id, status, closureReason, closedAt}}.
    """
    return await _gql_call(
        "mutation($b: ID!, $r: BugReportClosureReason!) { closeCollectionBugReport(bugReportId: $b, closureReason: $r) { ... on CloseCollectionBugReportMutationPayload { collectionBugReport { id status closureReason closedAt } } } }",
        {"b": str(bug_report_id), "r": closure_reason},
    )


@mcp.tool(name="nexus_submit_moderation_fix", annotations={**_MUTATING_ANNOTATIONS, "title": "Submit a moderation fix (v2)"})
async def nexus_submit_moderation_fix(
    moderation_id: int = Field(..., description="Moderation ID to fix.", ge=1),
    description: Optional[str] = Field(default=None, description="Description of the fix."),
) -> str:
    """Submit a fix for an active moderation on your content (v2 GraphQL).

    Returns:
        JSON {success, moderationFix: {id, status, description, createdAt}}.
    """
    return await _gql_call(
        "mutation($m: ID!, $d: String) { submitModerationFix(moderationId: $m, description: $d) { ... on SubmitModerationFixMutationPayload { success moderationFix { id status description createdAt } } } }",
        {"m": str(moderation_id), "d": _opt(description)},
    )


def _collection_manifest(
    name: str,
    domain_name: str,
    author: str,
    summary: Optional[str],
    description: Optional[str],
    author_url: Optional[str],
    game_versions: Optional[str],
    mods_json: Optional[str],
) -> dict[str, Any]:
    info: dict[str, Any] = {"name": name, "domainName": domain_name, "author": author}
    if summary is not None:
        info["summary"] = summary
    if description is not None:
        info["description"] = description
    if author_url is not None:
        info["authorUrl"] = author_url
    if game_versions is not None:
        info["gameVersions"] = _split_ids(game_versions)
    mods: list[Any] = []
    if mods_json is not None:
        mods = json.loads(mods_json)
    return {"info": info, "mods": mods}


@mcp.tool(name="nexus_create_collection", annotations={**_MUTATING_ANNOTATIONS, "title": "Create a collection (v2)"})
async def nexus_create_collection(
    name: str = Field(..., description="Collection name.", min_length=1),
    domain_name: str = Field(..., description="Game domain name, e.g. 'skyrimspecialedition'."),
    author: str = Field(..., description="Author display name."),
    summary: Optional[str] = Field(default=None, description="Short summary."),
    description: Optional[str] = Field(default=None, description="Long description (BBCode)."),
    author_url: Optional[str] = Field(default=None, description="Author profile URL."),
    game_versions: Optional[str] = Field(default=None, description="Comma-separated game versions."),
    adult_content: bool = Field(default=False, description="Whether the collection contains adult resources."),
    collection_schema_id: Optional[int] = Field(default=None, description="Collection schema ID."),
    mods_json: Optional[str] = Field(default=None, description="JSON array of mods: [{name, version, optional, domainName, source: {type: nexus|direct|browse|manual|bundle, modId, fileId, md5, fileSize, updatePolicy, logicalFilename, fileExpression, url, adultContent}, author}]. Required for a revision."),
    collection_uuid: Optional[str] = Field(default=None, description="Client UUID for the collection. Auto-generated if omitted."),
    collection_data_json: Optional[str] = Field(default=None, description="Full CollectionPayload JSON overriding all other params."),
) -> str:
    """Create a new collection and its first draft revision (v2 GraphQL).

    Either build the payload from the individual params or pass the full
    CollectionPayload JSON via collection_data_json (shape: {adultContent,
    collectionManifest: {info: {...}, mods: [...]}, collectionSchemaId}).

    Returns:
        JSON {success, collectionId, revisionId} or an error string.
    """
    if collection_data_json is not None:
        payload = json.loads(collection_data_json)
    else:
        payload = {
            "adultContent": bool(adult_content),
            "collectionManifest": _collection_manifest(
                name, domain_name, author, summary, description, author_url,
                game_versions, mods_json,
            ),
        }
        if collection_schema_id is not None:
            payload["collectionSchemaId"] = collection_schema_id
    uuid_val = _opt(collection_uuid) or str(uuid.uuid4())
    return await _gql_call(
        "mutation($c: CollectionPayload!, $u: String!) { createCollection(collectionData: $c, uuid: $u) { ... on CreateCollectionMutationPayload { success collectionId revisionId } } }",
        {"c": payload, "u": uuid_val},
    )


@mcp.tool(name="nexus_edit_collection", annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Edit a collection (v2)"})
async def nexus_edit_collection(
    collection_id: int = Field(..., description="Collection ID.", ge=1),
    name: Optional[str] = Field(default=None, description="New name."),
    summary: Optional[str] = Field(default=None, description="New summary."),
    description: Optional[str] = Field(default=None, description="New description (BBCode)."),
    category_id: Optional[int] = Field(default=None, description="New category ID."),
    allow_user_media: Optional[bool] = Field(default=None, description="Allow user media."),
    manually_verify_media: Optional[bool] = Field(default=None, description="Manually verify media."),
) -> str:
    """Edit a collection's metadata (must own the collection) via v2 GraphQL.

    Only the provided fields are changed; omitted fields stay as-is.

    Returns:
        JSON {success, collection: {id, name, slug}} or an error string.
    """
    args = _inline_args(
        collectionId=collection_id,
        name=name,
        summary=summary,
        description=description,
        categoryId=category_id,
        allowUserMedia=allow_user_media,
        manuallyVerifyMedia=manually_verify_media,
    )
    return await _gql_call(
        f"mutation {{ editCollection({args}) {{ ... on EditCollectionMutationPayload {{ success collection {{ id name slug }} }} }} }}"
    )


@mcp.tool(
    name="nexus_create_or_update_revision",
    annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Create/update a collection revision (v2)"},
)
async def nexus_create_or_update_revision(
    collection_id: int = Field(..., description="Collection ID.", ge=1),
    mods_json: Optional[str] = Field(default=None, description="JSON array of mods: [{name, version, optional, domainName, source: {...}, author}]."),
    name: Optional[str] = Field(default=None, description="Collection name override."),
    summary: Optional[str] = Field(default=None, description="Summary override."),
    description: Optional[str] = Field(default=None, description="Description override."),
    domain_name: Optional[str] = Field(default=None, description="Game domain override."),
    author: Optional[str] = Field(default=None, description="Author override."),
    author_url: Optional[str] = Field(default=None, description="Author URL override."),
    game_versions: Optional[str] = Field(default=None, description="Comma-separated game versions override."),
    adult_content: bool = Field(default=False, description="Whether the revision contains adult resources."),
    collection_uuid: Optional[str] = Field(default=None, description="Client UUID. Auto-generated if omitted."),
    collection_data_json: Optional[str] = Field(default=None, description="Full CollectionPayload JSON overriding all other params."),
) -> str:
    """Create a new draft revision or update the existing draft (v2 GraphQL).

    Pass mods_json (or the full payload via collection_data_json) to replace
    the revision's mod list; omit mods_json to keep it unchanged.

    Returns:
        JSON {success, collectionId, revisionId, revisionNumber} or an error string.
    """
    if collection_data_json is not None:
        payload = json.loads(collection_data_json)
    else:
        payload = {
            "adultContent": bool(adult_content),
            "collectionManifest": _collection_manifest(
                name or "", domain_name or "", author or "", summary, description,
                author_url, game_versions, mods_json,
            ),
        }
    uuid_val = _opt(collection_uuid) or str(uuid.uuid4())
    return await _gql_call(
        "mutation($c: CollectionPayload!, $i: Int!, $u: String!) { createOrUpdateRevision(collectionData: $c, collectionId: $i, uuid: $u) { ... on CreateOrUpdateRevisionMutationPayload { success collectionId revisionId revisionNumber } } }",
        {"c": payload, "i": collection_id, "u": uuid_val},
    )


@mcp.tool(name="nexus_update_revision", annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Update a revision (v2)"})
async def nexus_update_revision(
    revision_id: int = Field(..., description="Revision ID.", ge=1),
    installation_info: Optional[str] = Field(default=None, description="Installation instructions."),
    adult_content: Optional[bool] = Field(default=None, description="Whether the revision contains adult resources."),
) -> str:
    """Update a collection revision's metadata (must own it) via v2 GraphQL.

    Only the provided fields are changed; omitted fields stay as-is.

    Returns:
        JSON {success, revisionId} or an error string.
    """
    args = _inline_args(
        revisionId=revision_id,
        installationInfo=installation_info,
        adultContent=adult_content,
    )
    return await _gql_call(
        f"mutation {{ updateRevision({args}) {{ ... on UpdateRevisionMutationPayload {{ success revisionId }} }} }}"
    )


@mcp.tool(name="nexus_publish_revision", annotations={**_DESTRUCTIVE_ANNOTATIONS, "title": "Publish a revision (v2)"})
async def nexus_publish_revision(
    revision_id: int = Field(..., description="Revision ID.", ge=1),
    collection_status: Optional[Literal["listed", "unlisted", "under_moderation", "discarded"]] = Field(default=None, description="Status to publish with."),
    has_adult_resources: Optional[bool] = Field(default=None, description="Whether the revision contains adult resources."),
) -> str:
    """Publish a draft collection revision (must own the collection) via v2 GraphQL.

    This makes the revision publicly available - hard to undo (use
    nexus_retract_revision afterwards).

    Returns:
        JSON {success} or an error string.
    """
    args = _inline_args(
        revisionId=revision_id,
        collectionStatus=collection_status,
        hasAdultResources=has_adult_resources,
    )
    return await _gql_call(
        f"mutation {{ publishRevision({args}) {{ ... on PublishRevisionMutationPayload {{ success }} }} }}"
    )


@mcp.tool(name="nexus_retract_revision", annotations={**_DESTRUCTIVE_ANNOTATIONS, "title": "Retract a revision (v2)"})
async def nexus_retract_revision(
    revision_id: int = Field(..., description="Revision ID.", ge=1),
    reason: str = Field(..., description="Retraction reason.", min_length=1),
) -> str:
    """Retract a published collection revision (must own it) via v2 GraphQL.

    Returns:
        JSON {success} or an error string.
    """
    args = _inline_args(revisionId=revision_id, reason=reason)
    return await _gql_call(
        f"mutation {{ retractRevision({args}) {{ ... on RetractRevisionMutationPayload {{ success }} }} }}"
    )


@mcp.tool(name="nexus_discard_revision", annotations={**_DESTRUCTIVE_ANNOTATIONS, "title": "Discard a revision (v2)"})
async def nexus_discard_revision(
    collection_id: int = Field(..., description="Collection ID.", ge=1),
    revision_number: int = Field(..., description="Revision number.", ge=1),
    reason: Optional[str] = Field(default=None, description="Discard reason."),
) -> str:
    """Discard a collection revision (must own the collection) via v2 GraphQL.

    Returns:
        JSON {success} or an error string.
    """
    args = _inline_args(
        collectionId=collection_id, revisionNumber=revision_number, reason=reason
    )
    return await _gql_call(
        f"mutation {{ discardRevision({args}) {{ ... on DiscardRevisionMutationPayload {{ success }} }} }}"
    )


@mcp.tool(name="nexus_discard_collection", annotations={**_DESTRUCTIVE_ANNOTATIONS, "title": "Discard a collection (v2)"})
async def nexus_discard_collection(
    collection_id: int = Field(..., description="Collection ID.", ge=1),
    reason: str = Field(..., description="Discard reason.", min_length=1),
) -> str:
    """Discard (soft-delete) an entire collection (must own it) via v2 GraphQL.

    DESTRUCTIVE: discards the collection. Prefer nexus_unlist_collection.

    Returns:
        JSON {success} or an error string.
    """
    args = _inline_args(collectionId=collection_id, reason=reason)
    return await _gql_call(
        f"mutation {{ discardCollection({args}) {{ ... on DiscardCollectionMutationPayload {{ success }} }} }}"
    )


@mcp.tool(name="nexus_list_collection", annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "List a collection (v2)"})
async def nexus_list_collection(
    collection_id: int = Field(..., description="Collection ID.", ge=1),
) -> str:
    """List (publish) a currently unlisted collection (must own it) via v2 GraphQL.

    Returns:
        JSON {success} or an error string.
    """
    return await _gql_call(
        "mutation($i: Int!) { listCollection(collectionId: $i) { ... on ListCollectionMutationPayload { success } } }",
        {"i": collection_id},
    )


@mcp.tool(name="nexus_unlist_collection", annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Unlist a collection (v2)"})
async def nexus_unlist_collection(
    collection_id: int = Field(..., description="Collection ID.", ge=1),
) -> str:
    """Unlist a collection (hide from public listings, keep URL) via v2 GraphQL.

    Returns:
        JSON {success} or an error string.
    """
    return await _gql_call(
        "mutation($i: Int!) { unlistCollection(collectionId: $i) { ... on UnlistCollectionMutationPayload { success } } }",
        {"i": str(collection_id)},
    )


@mcp.tool(name="nexus_create_changelog", annotations={**_MUTATING_ANNOTATIONS, "title": "Create a changelog (v2)"})
async def nexus_create_changelog(
    revision_id: int = Field(..., description="Revision ID.", ge=1),
    description: str = Field(..., description="Changelog text.", min_length=1),
) -> str:
    """Create a changelog entry for a collection revision (must own it) via v2 GraphQL.

    Returns:
        JSON {success, changelogId} or an error string.
    """
    return await _gql_call(
        "mutation($r: ID!, $d: String!) { createChangelog(revisionId: $r, description: $d) { ... on CreateChangelogMutationPayload { success changelogId } } }",
        {"r": str(revision_id), "d": description},
    )


@mcp.tool(name="nexus_update_changelog", annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Update a changelog (v2)"})
async def nexus_update_changelog(
    changelog_id: int = Field(..., description="Changelog ID.", ge=1),
    description: str = Field(..., description="New changelog text.", min_length=1),
) -> str:
    """Update an existing changelog entry (must own it) via v2 GraphQL.

    Returns:
        JSON {success, changelogId} or an error string.
    """
    return await _gql_call(
        "mutation($c: ID!, $d: String!) { updateChangelog(changelogId: $c, description: $d) { ... on UpdateChangelogMutationPayload { success changelogId } } }",
        {"c": str(changelog_id), "d": description},
    )


# ---------------------------------------------------------------------------
# Tools: v2 GraphQL - tag / badge / moderation mutations
# ---------------------------------------------------------------------------


@mcp.tool(name="nexus_create_tag", annotations={**_MUTATING_ANNOTATIONS, "title": "Create a tag (v2)"})
async def nexus_create_tag(
    name: str = Field(..., description="Tag name.", min_length=1),
    category_id: Optional[int] = Field(default=None, description="Tag category ID."),
    game_ids: Optional[str] = Field(default=None, description="Comma-separated game IDs to attach."),
    global_tag: Optional[bool] = Field(default=None, description="Create as global tag."),
    adult: Optional[bool] = Field(default=None, description="Adult content tag."),
) -> str:
    """Create a new tag (moderator permissions may be required) via v2 GraphQL.

    Returns:
        JSON {success, tag: {id, name}} or an error string.
    """
    args = _inline_args(
        name=name,
        categoryId=category_id,
        gameIds=[int(g) for g in _split_ids(game_ids) if g.isdigit()] or None if game_ids is not None else None,
        global_=None,
        adult=adult,
    )
    if _opt(global_tag) is not None:
        args += f", global: {_qlit(bool(global_tag))}"
    return await _gql_call(
        f"mutation {{ createTag({args}) {{ ... on CreateTagMutationPayload {{ success tag {{ id name }} }} }} }}"
    )


@mcp.tool(name="nexus_update_tag", annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Update a tag (v2)"})
async def nexus_update_tag(
    tag_id: int = Field(..., description="Tag ID.", ge=1),
    name: Optional[str] = Field(default=None, description="New tag name."),
    category_id: Optional[int] = Field(default=None, description="New tag category ID."),
    game_ids: Optional[str] = Field(default=None, description="Comma-separated game IDs to attach."),
    global_tag: Optional[bool] = Field(default=None, description="Mark as global tag."),
    adult: Optional[bool] = Field(default=None, description="Adult content tag."),
) -> str:
    """Update an existing tag (moderator permissions may be required) via v2 GraphQL.

    Only the provided fields are changed; omitted fields stay as-is.

    Returns:
        JSON {success, tag: {id, name}} or an error string.
    """
    args = _inline_args(
        id=tag_id,
        name=name,
        categoryId=category_id,
        gameIds=[int(g) for g in _split_ids(game_ids) if g.isdigit()] or None if game_ids is not None else None,
        adult=adult,
    )
    if _opt(global_tag) is not None:
        args += f", global: {_qlit(bool(global_tag))}"
    return await _gql_call(
        f"mutation {{ updateTag({args}) {{ ... on UpdateTagMutationPayload {{ success tag {{ id name }} }} }} }}"
    )


@mcp.tool(name="nexus_discard_tag", annotations={**_DESTRUCTIVE_ANNOTATIONS, "title": "Discard a tag (v2)"})
async def nexus_discard_tag(
    tag_id: int = Field(..., description="Tag ID.", ge=1),
) -> str:
    """Discard (soft-delete) a tag (moderator permissions may be required) via v2 GraphQL.

    Returns:
        JSON {success} or an error string.
    """
    return await _gql_call(
        "mutation($id: ID!) { discardTag(id: $id) { ... on DiscardTagMutationPayload { success } } }",
        {"id": str(tag_id)},
    )


@mcp.tool(
    name="nexus_add_badge_to_collection",
    annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Add a badge to a collection (v2)"},
)
async def nexus_add_badge_to_collection(
    badge_id: int = Field(..., description="Badge ID (see nexus_get_badges).", ge=1),
    collection_id: int = Field(..., description="Collection ID.", ge=1),
) -> str:
    """Award a badge to a collection (moderator permissions required) via v2 GraphQL.

    Returns:
        JSON {success} or an error string.
    """
    return await _gql_call(
        "mutation($b: ID!, $c: Int!) { addBadgeToCollection(badgeId: $b, collectionId: $c) { ... on AddBadgeToCollectionMutationPayload { success } } }",
        {"b": str(badge_id), "c": collection_id},
    )


@mcp.tool(
    name="nexus_remove_badge_from_collection",
    annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Remove a badge from a collection (v2)"},
)
async def nexus_remove_badge_from_collection(
    badge_id: int = Field(..., description="Badge ID (see nexus_get_badges).", ge=1),
    collection_id: int = Field(..., description="Collection ID.", ge=1),
) -> str:
    """Remove a badge from a collection (moderator permissions required) via v2 GraphQL.

    Returns:
        JSON {success} or an error string.
    """
    return await _gql_call(
        "mutation($b: ID!, $c: Int!) { removeBadgeFromCollection(badgeId: $b, collectionId: $c) { ... on RemoveBadgeFromCollectionMutationPayload { success } } }",
        {"b": str(badge_id), "c": collection_id},
    )


@mcp.tool(name="nexus_reorder_item", annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Reorder collection media (v2)"})
async def nexus_reorder_item(
    id: int = Field(..., description="ID of the item (collection image/video) to move.", ge=1),
    target_id: int = Field(..., description="ID of the item to position relative to.", ge=1),
    location: Literal["BEFORE", "AFTER"] = Field(..., description="Position relative to target."),
) -> str:
    """Reorder reorderable items (collection images/videos) via v2 GraphQL.

    Requires ownership of the parent collection.

    Returns:
        JSON {item: {__typename}} or an error string.
    """
    return await _gql_call(
        "mutation($i: ID!, $t: ID!, $l: ReorderLocation!) { reorderItem(id: $i, targetId: $t, location: $l) { ... on ReorderItemPayload { item { __typename } } } }",
        {"i": str(id), "t": str(target_id), "l": location},
    )


# ---------------------------------------------------------------------------
# Tools: v2 GraphQL - comment moderation mutations
# ---------------------------------------------------------------------------


@mcp.tool(name="nexus_hide_comment", annotations={**_DESTRUCTIVE_ANNOTATIONS, "title": "Hide a comment (v2)"})
async def nexus_hide_comment(
    comment_id: int = Field(..., description="Comment ID to hide.", ge=1),
    reason: str = Field(..., description="Public reason for hiding.", min_length=1),
    internal_reason: Optional[str] = Field(default=None, description="Internal reason (moderators only)."),
) -> str:
    """Hide a comment (moderator permissions required) via v2 GraphQL.

    Returns:
        JSON {comment: {id}} or an error string.
    """
    args = _inline_args(commentId=comment_id, reason=reason, internalReason=internal_reason)
    return await _gql_call(
        f"mutation {{ hideComment({args}) {{ ... on HideCommentMutationPayload {{ comment {{ id }} }} }} }}"
    )


@mcp.tool(name="nexus_lock_comment", annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Lock a comment (v2)"})
async def nexus_lock_comment(
    comment_id: int = Field(..., description="Comment ID to lock.", ge=1),
) -> str:
    """Lock a comment against further interaction (moderator permissions) via v2 GraphQL.

    Returns:
        JSON {comment: {id}} or an error string.
    """
    return await _gql_call(
        "mutation($id: ID!) { lockComment(commentId: $id) { ... on LockCommentMutationPayload { comment { id } } } }",
        {"id": str(comment_id)},
    )


@mcp.tool(name="nexus_lock_comment_thread", annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Lock a comment thread (v2)"})
async def nexus_lock_comment_thread(
    comment_thread_id: int = Field(..., description="Comment thread ID to lock.", ge=1),
) -> str:
    """Lock a comment thread (e.g. a mod's comments page) via v2 GraphQL.

    Requires moderator permissions or thread ownership.

    Returns:
        JSON {commentThread: {id, lockedAt}} or an error string.
    """
    return await _gql_call(
        "mutation($id: ID!) { lockCommentThread(commentThreadId: $id) { ... on LockThreadMutationPayload { commentThread { id lockedAt } } } }",
        {"id": str(comment_thread_id)},
    )


@mcp.tool(name="nexus_pin_comment", annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Pin a comment (v2)"})
async def nexus_pin_comment(
    comment_id: int = Field(..., description="Comment ID to pin.", ge=1),
) -> str:
    """Pin a comment to the top of its thread via v2 GraphQL.

    Requires moderator permissions or thread ownership.

    Returns:
        JSON {comment: {id}} or an error string.
    """
    return await _gql_call(
        "mutation($id: ID!) { pinComment(commentId: $id) { ... on PinCommentMutationPayload { comment { id } } } }",
        {"id": str(comment_id)},
    )


@mcp.tool(name="nexus_unpin_comment", annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Unpin a comment (v2)"})
async def nexus_unpin_comment(
    comment_id: int = Field(..., description="Comment ID to unpin.", ge=1),
) -> str:
    """Unpin a previously pinned comment via v2 GraphQL.

    Requires moderator permissions or thread ownership.

    Returns:
        JSON {comment: {id}} or an error string.
    """
    return await _gql_call(
        "mutation($id: ID!) { unpinComment(commentId: $id) { ... on UnpinCommentMutationPayload { comment { id } } } }",
        {"id": str(comment_id)},
    )


@mcp.tool(
    name="nexus_reorder_pinned_comments",
    annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Reorder pinned comments (v2)"},
)
async def nexus_reorder_pinned_comments(
    comment_ids: str = Field(..., description="Comma-separated pinned comment IDs in the desired order."),
) -> str:
    """Reorder pinned comments in a thread via v2 GraphQL.

    Pass ALL pinned comment IDs of the thread in the desired order.

    Returns:
        JSON {comments: [{id}]} in the new order, or an error string.
    """
    ids = [int(x) for x in _split_ids(comment_ids) if x.isdigit()]
    if not ids:
        return "Error: no valid comment IDs."
    return await _gql_call(
        "mutation($ids: [ID!]!) { reorderPinnedComments(commentIds: $ids) { ... on ReorderPinnedCommentsMutationPayload { comments { id } } } }",
        {"ids": [str(i) for i in ids]},
    )


@mcp.tool(
    name="nexus_clear_comment_moderation_status",
    annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Clear comment moderation status (v2)"},
)
async def nexus_clear_comment_moderation_status(
    comment_id: int = Field(..., description="Comment ID.", ge=1),
) -> str:
    """Clear a comment's moderation status (moderator permissions) via v2 GraphQL.

    Returns:
        JSON {comment: {id}} or an error string.
    """
    return await _gql_call(
        "mutation($id: ID!) { clearCommentModerationStatus(commentId: $id) { ... on ClearCommentModerationStatusMutationPayload { comment { id } } } }",
        {"id": str(comment_id)},
    )


@mcp.tool(
    name="nexus_clear_comment_thread_moderation_status",
    annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Clear thread moderation status (v2)"},
)
async def nexus_clear_comment_thread_moderation_status(
    comment_thread_id: int = Field(..., description="Comment thread ID.", ge=1),
) -> str:
    """Clear a comment thread's moderation status (moderator permissions) via v2 GraphQL.

    Returns:
        JSON {commentThread: {id}} or an error string.
    """
    return await _gql_call(
        "mutation($id: ID!) { clearCommentThreadModerationStatus(commentThreadId: $id) { ... on ClearThreadModerationStatusMutationPayload { commentThread { id } } } }",
        {"id": str(comment_thread_id)},
    )


# ---------------------------------------------------------------------------
# Tools: v2 GraphQL - metrics / donation points / uploads
# ---------------------------------------------------------------------------


@mcp.tool(name="nexus_track_app_metric", annotations={**_MUTATING_ANNOTATIONS, "title": "Track an app metric (v2)"})
async def nexus_track_app_metric(
    event_type: Literal["collection_started", "collection_completed"] = Field(..., description="Metric event type."),
    entity_type: Literal["collection"] = Field(..., description="Metric entity type."),
    entity_id: str = Field(..., description="Entity ID (e.g. collection id).", min_length=1),
    client_string: Optional[str] = Field(default=None, description="Client identifier string."),
    metadata_json: Optional[str] = Field(default=None, description="Optional JSON metadata object."),
) -> str:
    """Report an app metric (e.g. a Vortex collection install event) via v2 GraphQL.

    Returns:
        JSON {success, errors} or an error string.
    """
    metadata: Any = None
    if metadata_json is not None:
        try:
            metadata = json.loads(metadata_json)
        except json.JSONDecodeError:
            return "Error: metadata_json must be a valid JSON object string."
    return await _gql_call(
        "mutation($e: AppMetricEventType!, $t: AppMetricEntityType!, $i: String!, $c: String, $m: JSON) { trackAppMetric(eventType: $e, entityType: $t, entityId: $i, clientString: $c, metadata: $m) { ... on TrackAppMetricMutationPayload { success errors } } }",
        {"e": event_type, "t": entity_type, "i": entity_id, "c": _opt(client_string), "m": metadata},
    )


@mcp.tool(
    name="nexus_block_mods_from_earning_dp",
    annotations={**_DESTRUCTIVE_ANNOTATIONS, "title": "Block a user's mods from earning DP (v2)"},
)
async def nexus_block_mods_from_earning_dp(
    user_id: Optional[int] = Field(default=None, description="User ID. Omit for the current user."),
) -> str:
    """Block a user's mods from earning Donation Points via v2 GraphQL.

    Requires moderator permissions when targeting another user.

    Returns:
        JSON {success} or an error string.
    """
    return await _gql_call(
        "mutation($u: ID) { blockModsFromEarningDp(userId: $u) { ... on BlockModsFromEarningDpMutationPayload { success } } }",
        {"u": _opt(user_id)},
    )


@mcp.tool(
    name="nexus_unblock_mods_from_earning_dp",
    annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Unblock a user's mods from earning DP (v2)"},
)
async def nexus_unblock_mods_from_earning_dp(
    user_id: Optional[int] = Field(default=None, description="User ID. Omit for the current user."),
) -> str:
    """Unblock a user's mods from earning Donation Points via v2 GraphQL.

    Requires moderator permissions when targeting another user.

    Returns:
        JSON {success} or an error string.
    """
    return await _gql_call(
        "mutation($u: ID) { unblockModsFromEarningDp(userId: $u) { ... on UnblockModsFromEarningDpMutationPayload { success } } }",
        {"u": _opt(user_id)},
    )


@mcp.tool(name="nexus_upload_attachment", annotations={**_MUTATING_ANNOTATIONS, "title": "Upload an attachment (v2)"})
async def nexus_upload_attachment(
    filename: str = Field(..., description="File name including extension.", min_length=1),
    content_base64: str = Field(..., description="Base64-encoded file content.", min_length=1),
    mime_type: str = Field(default="application/octet-stream", description="MIME type."),
) -> str:
    """Upload an attachment (usable in comments) via the v2 GraphQL multipart spec.

    Accepts base64-encoded content and posts it as an Apollo Upload multipart
    request. Attachments can then be referenced by id in comment mutations.

    Returns:
        JSON {attachment: {id, filename, url}} or an error string.
    """
    try:
        content = base64.b64decode(content_base64, validate=True)
    except Exception:
        return "Error: content_base64 is not valid base64."
    query = (
        "mutation($file: Upload!) { uploadAttachment(file: $file) "
        "{ ... on UploadAttachmentMutationPayload { attachment { id filename url } } } }"
    )
    operations = json.dumps({"query": query, "variables": {"file": None}})
    file_map = json.dumps({"0": ["variables.file"]})
    try:
        headers = await _auth_headers()
        async with httpx.AsyncClient(timeout=300) as client:
            response = await client.post(
                API_BASE + GRAPHQL_PATH,
                headers=headers,
                data={"operations": operations, "map": file_map},
                files={"0": (filename, content, mime_type)},
            )
        payload = response.json()
    except NexusApiError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error: upload failed: {exc}"
    if response.status_code != 200 or payload.get("errors"):
        errors = json.dumps(payload.get("errors") or payload)[:500]
        return f"Error: HTTP {response.status_code} {errors}"
    data = payload.get("data") or {}
    return json.dumps(data, indent=2, ensure_ascii=False)
