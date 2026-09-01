"""Tools: v2 GraphQL user interactions - tracking, kudos, favourites, comment likes/CRUD."""

from __future__ import annotations

from pydantic import Field

from .._annotations import (
    _DESTRUCTIVE_ANNOTATIONS,
    _DESTRUCTIVE_IDEMPOTENT_ANNOTATIONS,
    _IDEMPOTENT_MUTATION_ANNOTATIONS,
    _MUTATING_ANNOTATIONS,
)
from .._core import (
    _gql_call,
)
from .._server import mcp


@mcp.tool(name="nexus_track_user", annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Track a user (v2)"})
async def nexus_track_user(
    user_id: int = Field(..., description="Nexus Mods member ID to track for updates.", ge=1),
) -> str:
    """Start tracking a user (get notifications about their new mods) via v2 GraphQL.

    Consumes the v2 GraphQL pool, NOT the v1 REST rate-limit quota.

    Returns:
        JSON {trackUser: {success}} or an error string.
    """
    return await _gql_call(
        "mutation($id: ID!) { trackUser(trackedUserId: $id) { ... on TrackUserMutationPayload { success } } }",
        {"id": str(user_id)},
    )


@mcp.tool(name="nexus_untrack_user", annotations={**_DESTRUCTIVE_IDEMPOTENT_ANNOTATIONS, "title": "Untrack a user (v2)"})
async def nexus_untrack_user(
    user_id: int = Field(..., description="Nexus Mods member ID to stop tracking.", ge=1),
) -> str:
    """Stop tracking a user via v2 GraphQL.

    Consumes the v2 GraphQL pool, NOT the v1 REST rate-limit quota.

    Returns:
        JSON {untrackUser: {success}} or an error string.
    """
    return await _gql_call(
        "mutation($id: ID!) { untrackUser(trackedUserId: $id) { ... on UntrackUserMutationPayload { success } } }",
        {"id": str(user_id)},
    )


@mcp.tool(name="nexus_give_kudos", annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Give kudos to a user (v2)"})
async def nexus_give_kudos(
    user_id: int = Field(..., description="Nexus Mods member ID to give kudos to.", ge=1),
) -> str:
    """Give kudos to a user via v2 GraphQL.

    Consumes the v2 GraphQL pool, NOT the v1 REST rate-limit quota.

    Returns:
        JSON {giveKudos: {success}} or an error string.
    """
    return await _gql_call(
        "mutation($id: ID!) { giveKudos(kudosUserId: $id) { ... on GiveKudosMutationPayload { success } } }",
        {"id": str(user_id)},
    )


@mcp.tool(name="nexus_remove_kudos", annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Remove kudos from a user (v2)"})
async def nexus_remove_kudos(
    user_id: int = Field(..., description="Nexus Mods member ID to remove kudos from.", ge=1),
) -> str:
    """Remove previously given kudos from a user via v2 GraphQL.

    Consumes the v2 GraphQL pool, NOT the v1 REST rate-limit quota.

    Returns:
        JSON {removeKudos: {success}} or an error string.
    """
    return await _gql_call(
        "mutation($id: ID!) { removeKudos(kudosUserId: $id) { ... on RemoveKudosMutationPayload { success } } }",
        {"id": str(user_id)},
    )


@mcp.tool(name="nexus_add_favourite_game", annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Favourite a game (v2)"})
async def nexus_add_favourite_game(
    game_id: int = Field(..., description="Game ID (from nexus_search_games / nexus_get_game 'id').", ge=1),
) -> str:
    """Add a game to your favourites via v2 GraphQL.

    Consumes the v2 GraphQL pool, NOT the v1 REST rate-limit quota.

    Returns:
        JSON {addFavouriteGame: {success}} or an error string.
    """
    return await _gql_call(
        "mutation($id: ID!) { addFavouriteGame(gameId: $id) { ... on AddFavouriteGameMutationPayload { success } } }",
        {"id": str(game_id)},
    )


@mcp.tool(name="nexus_remove_favourite_game", annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Unfavourite a game (v2)"})
async def nexus_remove_favourite_game(
    game_id: int = Field(..., description="Game ID to remove from favourites.", ge=1),
) -> str:
    """Remove a game from your favourites via v2 GraphQL.

    Consumes the v2 GraphQL pool, NOT the v1 REST rate-limit quota.

    Returns:
        JSON {removeFavouriteGame: {success}} or an error string.
    """
    return await _gql_call(
        "mutation($id: ID!) { removeFavouriteGame(gameId: $id) { ... on RemoveFavouriteGameMutationPayload { success } } }",
        {"id": str(game_id)},
    )


@mcp.tool(name="nexus_like_comment", annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Like a comment (v2)"})
async def nexus_like_comment(
    comment_id: int = Field(..., description="Comment ID to like.", ge=1),
) -> str:
    """Like a comment via v2 GraphQL.

    Consumes the v2 GraphQL pool, NOT the v1 REST rate-limit quota.

    Returns:
        JSON {likeComment: {comment}} or an error string.
    """
    return await _gql_call(
        "mutation($id: ID!) { likeComment(commentId: $id) { ... on LikeCommentMutationPayload { comment { id likesCount } } } }",
        {"id": str(comment_id)},
    )


@mcp.tool(name="nexus_remove_comment_like", annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Unlike a comment (v2)"})
async def nexus_remove_comment_like(
    comment_id: int = Field(..., description="Comment ID to remove your like from.", ge=1),
) -> str:
    """Remove your like from a comment via v2 GraphQL.

    Consumes the v2 GraphQL pool, NOT the v1 REST rate-limit quota.

    Returns:
        JSON {removeCommentLike: {comment}} or an error string.
    """
    return await _gql_call(
        "mutation($id: ID!) { removeCommentLike(commentId: $id) { ... on RemoveCommentLikeMutationPayload { comment { id likesCount } } } }",
        {"id": str(comment_id)},
    )


@mcp.tool(name="nexus_create_comment", annotations={**_MUTATING_ANNOTATIONS, "title": "Post a comment (v2)"})
async def nexus_create_comment(
    thread_id: int = Field(..., description="Comment thread ID to reply in.", ge=1),
    body: str = Field(..., description="Comment body text (plain text).", min_length=1),
    reply_to_id: int | None = Field(None, description="Comment ID to reply to; omit for a top-level comment.", ge=1),
) -> str:
    """Post a comment in a thread via v2 GraphQL — top-level by default,
    or a nested reply when reply_to_id is given.

    Consumes the v2 GraphQL pool, NOT the v1 REST rate-limit quota.
    Find thread IDs on mod pages (?tab=posts); forum threads and mod
    posts have distinct thread IDs.

    Returns:
        JSON {createComment: {comment: {id, body, ...}}} or an error string.
    """
    if not isinstance(reply_to_id, int):
        reply_to_id = None  # direct-call artifact: unpassed Optional Field params arrive as FieldInfo
    return await _gql_call(
        "mutation($t: ID!, $b: String!, $r: ID) { createComment(commentThreadId: $t, body: $b, replyToId: $r) { ... on CreateCommentMutationPayload { comment { id body createdAt creator { name } } } } }",
        {"t": str(thread_id), "b": body, "r": str(reply_to_id) if reply_to_id is not None else None},
    )


@mcp.tool(name="nexus_edit_comment", annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Edit a comment (v2)"})
async def nexus_edit_comment(
    comment_id: int = Field(..., description="Comment ID to edit (must be your own comment).", ge=1),
    body: str = Field(..., description="New comment body text (plain text).", min_length=1),
) -> str:
    """Edit the body of your own comment via v2 GraphQL.

    Consumes the v2 GraphQL pool, NOT the v1 REST rate-limit quota.
    Only the comment's author can edit it.

    Returns:
        JSON {updateComment: {comment: {id, body, ...}}} or an error string.
    """
    return await _gql_call(
        "mutation($id: ID!, $b: String!) { updateComment(commentId: $id, body: $b) { ... on UpdateCommentMutationPayload { comment { id body createdAt creator { name } } } } }",
        {"id": str(comment_id), "b": body},
    )


@mcp.tool(name="nexus_discard_comment", annotations={**_DESTRUCTIVE_ANNOTATIONS, "title": "Discard a comment (v2)"})
async def nexus_discard_comment(
    comment_id: int = Field(..., description="Comment ID to discard (soft-delete).", ge=1),
) -> str:
    """Discard (soft-delete) a comment via v2 GraphQL.

    Consumes the v2 GraphQL pool, NOT the v1 REST rate-limit quota.
    Discarded comments are removed from public view. Only the author
    (or a moderator) can discard a comment.
    NOTE: restoring via nexus_restore_comment REQUIRES OAuth Bearer
    auth - Nexus denies restore under apikey-only auth, so with an API
    key alone discard is effectively one-way.

    Returns:
        JSON {discardComment: {comment: {id, isDiscarded, discardedAt}}} or an error string.
    """
    return await _gql_call(
        "mutation($id: ID!) { discardComment(commentId: $id) { ... on DiscardCommentMutationPayload { comment { id isDiscarded discardedAt } } } }",
        {"id": str(comment_id)},
    )


@mcp.tool(name="nexus_restore_comment", annotations={**_IDEMPOTENT_MUTATION_ANNOTATIONS, "title": "Restore a comment (v2)"})
async def nexus_restore_comment(
    comment_id: int = Field(..., description="Comment ID to restore (undo discard).", ge=1),
) -> str:
    """Restore a previously discarded comment via v2 GraphQL.

    Consumes the v2 GraphQL pool, NOT the v1 REST rate-limit quota.
    Undo for nexus_discard_comment — the comment becomes publicly
    visible again.
    REQUIRES OAuth Bearer auth: Nexus denies restore under apikey-only
    auth even for your own comments.

    Returns:
        JSON {restoreComment: {comment: {id, isDiscarded, discardedAt}}} or an error string.
    """
    return await _gql_call(
        "mutation($id: ID!) { restoreComment(commentId: $id) { ... on RestoreCommentMutationPayload { comment { id isDiscarded discardedAt } } } }",
        {"id": str(comment_id)},
    )


# ---------------------------------------------------------------------------
# Tools: v2 GraphQL reads & user preferences (batch 4)
# ---------------------------------------------------------------------------
