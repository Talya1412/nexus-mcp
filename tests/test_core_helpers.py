"""Unit tests for _core helpers: arg unwrapping, cache keys, dumps, PKCE, tokens."""
import base64
import hashlib
import json
import time

import httpx
import pytest
from pydantic.fields import FieldInfo

import nexus_mcp._core as core


def _field() -> FieldInfo:
    return FieldInfo()


class TestOpt:
    def test_fieldinfo_returns_default(self):
        assert core._opt(_field(), "fallback") == "fallback"

    def test_fieldinfo_default_none(self):
        assert core._opt(_field()) is None

    def test_plain_value_passthrough(self):
        assert core._opt("skyrim", "fallback") == "skyrim"

    def test_none_passthrough_is_not_default(self):
        # explicit None is a caller choice, distinct from unset
        assert core._opt(None, "fallback") is None


class TestQlitAndInlineArgs:
    def test_qlit_string(self):
        assert core._qlit("skyrim") == '"skyrim"'

    def test_qlit_int_bool(self):
        assert core._qlit(5) == "5"
        assert core._qlit(True) == "true"

    def test_inline_args_skips_unset(self):
        assert core._inline_args(term=_field(), count=5) == "count: 5"

    def test_inline_args_skips_explicit_none(self):
        assert core._inline_args(term=None, count=3) == "count: 3"

    def test_inline_args_multiple(self):
        out = core._inline_args(term="mage", count=10)
        assert out == "term: \"mage\", count: 10"

    def test_inline_args_empty(self):
        assert core._inline_args() == ""


class TestSplitIds:
    def test_single(self):
        assert core._split_ids("12345") == ["12345"]

    def test_multiple_with_spaces(self):
        assert core._split_ids("1, 2 ,3") == ["1", "2", "3"]

    def test_empty_and_trailing_comma(self):
        assert core._split_ids(", ,") == []
        assert core._split_ids("1,") == ["1"]


class TestTtlFor:
    def test_games_list(self):
        assert core._ttl_for("/v1/games.json") == 3600

    def test_single_game(self):
        assert core._ttl_for("/v1/games/skyrim.json") == 3600

    def test_user_endpoints_never_cached(self):
        assert core._ttl_for("/v1/users/validate.json") == 0
        assert core._ttl_for("/v1/user/tracked_mods.json") == 0

    def test_mods_and_files_cached_5min(self):
        assert core._ttl_for("/v1/games/skyrim/mods/1234.json") == 300
        assert core._ttl_for("/v1/games/skyrim/mods/1234/files.json") == 300

    def test_unknown_zero(self):
        assert core._ttl_for("/v1/something/else.json") == 0


class TestCacheKey:
    def test_order_independent(self):
        a = core._cache_key("GET", "/x", {"b": 2, "a": 1}, None)
        b = core._cache_key("GET", "/x", {"a": 1, "b": 2}, None)
        assert a == b

    def test_different_params_differ(self):
        assert core._cache_key("GET", "/x", {"a": 1}, None) != core._cache_key("GET", "/x", {"a": 2}, None)


class TestRlSnapshot:
    def test_extracts_mixed_case(self):
        resp = httpx.Response(200, headers={"x-rl-hourly-limit": "2500", "X-RL-Daily-Remaining": "19999"})
        rl = core._rl_snapshot(resp)
        assert rl["x-rl-hourly-limit"] == "2500"
        assert rl["x-rl-daily-remaining"] == "19999"

    def test_missing_headers_absent(self):
        assert core._rl_snapshot(httpx.Response(200)) == {}


class TestStatusHint:
    def test_known_statuses(self):
        for status in (400, 401, 403, 404, 410, 422):
            assert core._status_hint(status), f"no hint for {status}"

    def test_unknown_empty(self):
        assert core._status_hint(418) == ""


class TestDump:
    def test_dict_payload_gets_rl_key(self):
        out = json.loads(core._dump({"a": 1}, {"x": "y"}))
        assert out == {"a": 1, "_rl": {"x": "y"}}

    def test_list_payload_wrapped(self):
        out = json.loads(core._dump([1, 2], {}))
        assert out == {"result": [1, 2], "_rl": {}}


class TestGqlPage:
    def test_extracts_node_page(self):
        data = json.dumps({"mods": {"totalCount": 7, "nodes": [{"modId": 1}]}})
        out = json.loads(core._gql_page(data, "mods"))
        assert out["_returned"] == 1
        assert out["nodes"] == [{"modId": 1}]

    def test_non_json_passthrough(self):
        assert core._gql_page("Error: boom", "mods") == "Error: boom"

    def test_wrong_root_passthrough(self):
        data = json.dumps({"other": {}})
        assert json.loads(core._gql_page(data, "mods")) == {"other": {}}


class TestPkce:
    def test_verifier_length(self):
        verifier, _ = core._pkce_pair()
        assert len(verifier) >= 43  # RFC 7636 minimum

    def test_challenge_matches_verifier(self):
        verifier, challenge = core._pkce_pair()
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        assert challenge == expected

    def test_pairs_are_unique(self):
        assert core._pkce_pair() != core._pkce_pair()


class TestTokensFromReply:
    def test_full_reply(self):
        now = int(time.time())
        tokens = core._tokens_from_reply(
            {"access_token": "at", "refresh_token": "rt", "expires_in": 3600, "created_at": now}
        )
        assert tokens["access_token"] == "at"
        assert tokens["refresh_token"] == "rt"
        assert tokens["expires_at"] == pytest.approx(now + 3600 - core.OAUTH_REFRESH_MARGIN, abs=2)

    def test_defaults(self):
        tokens = core._tokens_from_reply({"access_token": "at"})
        assert tokens["token_type"] == "Bearer"
        assert tokens["refresh_token"] is None
        assert tokens["expires_at"] > time.time()  # 6h default minus margin
