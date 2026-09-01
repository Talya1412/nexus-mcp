"""Request/response pipeline tests with a mocked HTTP transport - no network access."""
import asyncio
import json

import httpx

import nexus_mcp._core as core


def run(coro):
    return asyncio.run(coro)


def install(handler):
    core._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=core.API_BASE
    )


class TestRestPipeline:
    def test_success_and_ttl_cache(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(200, json={"games": ["skyrim"]}, headers={"X-RL-Hourly-Remaining": "2500"})

        install(handler)
        p1, rl1 = run(core._api("GET", "/v1/games.json"))
        p2, _rl2 = run(core._api("GET", "/v1/games.json"))
        assert calls["n"] == 1, "second identical GET must be served from TTL cache"
        assert p1 == p2 == {"games": ["skyrim"]}
        assert rl1["x-rl-hourly-remaining"] == "2500"

    def test_post_not_cached(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(200, json={"ok": True})

        install(handler)
        run(core._api("POST", "/v1/whatever.json", data={"a": 1}))
        run(core._api("POST", "/v1/whatever.json", data={"a": 1}))
        assert calls["n"] == 2

    def test_error_404_with_hint(self):
        def handler(request):
            return httpx.Response(404, json={"message": "no such mod"})

        install(handler)
        out = run(core._call("GET", "/v1/games/skyrim/mods/999999.json"))
        assert out.startswith("Error: API error 404")
        assert "API says: no such mod" in out
        assert "mod_id" in out  # _status_hint(404) guidance

    def test_error_429_reports_quota(self):
        def handler(request):
            return httpx.Response(429, headers={"X-RL-Hourly-Remaining": "0"}, json={})

        install(handler)
        out = run(core._call("GET", "/v1/mods.json"))
        assert "Rate limit exceeded" in out
        assert "x-rl-hourly-remaining" in out

    def test_202_ambiguous_treated_as_timeout(self):
        def handler(request):
            return httpx.Response(202, json={})

        install(handler)
        out = run(core._call("POST", "/v1/endorse.json", data={"a": 1}))
        assert "202" in out and "may or may not" in out

    def test_non_json_200_is_firewall_hint(self):
        def handler(request):
            return httpx.Response(200, text="<html>blocked</html>")

        install(handler)
        out = run(core._call("GET", "/v1/odd.json"))
        assert "non-JSON" in out

    def test_timeout_maps_to_nexus_api_error(self):
        def handler(request):
            raise httpx.ConnectTimeout("boom", request=request)

        install(handler)
        out = run(core._call("GET", "/v1/games.json"))
        assert out.startswith("Error: Request timed out")

    def test_connect_error_maps_to_network_error(self):
        def handler(request):
            raise httpx.ConnectError("dns fail", request=request)

        install(handler)
        out = run(core._call("GET", "/v1/games.json"))
        assert "Network error: ConnectError" in out

    def test_apikey_header_sent(self):
        seen = {}

        def handler(request):
            seen["apikey"] = request.headers.get("apikey")
            return httpx.Response(200, json={})

        install(handler)
        run(core._call("GET", "/v1/games.json"))
        assert seen["apikey"] == "test-key-123"


class TestGraphqlPipeline:
    def test_success_and_cache(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            body = json.loads(request.content)
            assert body["query"].strip().startswith("query")
            return httpx.Response(200, json={"data": {"mods": {"totalCount": 0}}, "errors": None})

        install(handler)
        d1, _ = run(core._graphql("query { mods { totalCount } }"))
        d2, _ = run(core._graphql("query { mods { totalCount } }"))
        assert calls["n"] == 1, "identical GraphQL body must hit the 60s cache"
        assert d1 == d2 == {"mods": {"totalCount": 0}}

    def test_graphql_errors_surface(self):
        def handler(request):
            return httpx.Response(200, json={"errors": [{"message": "bad filter"}], "data": None})

        install(handler)
        out = run(core._gql_call("query { mods { totalCount } }"))
        assert out == "Error: GraphQL query failed: bad filter"

    def test_graphql_http_error(self):
        def handler(request):
            return httpx.Response(500, text="server exploded")

        install(handler)
        out = run(core._gql_call("query { mods { totalCount } }"))
        assert out.startswith("Error: GraphQL error 500")

    def test_graphql_non_json(self):
        def handler(request):
            return httpx.Response(200, text="<html>cdn</html>")

        install(handler)
        out = run(core._gql_call("query { mods { totalCount } }"))
        assert "non-JSON" in out

    def test_mutation_not_cached(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(200, json={"data": {"updateModDirectDownloadEnabled": {"success": True}}})

        install(handler)
        q = "mutation($u: ID!, $e: Boolean!) { updateModDirectDownloadEnabled(modUid: $u, directDownloadEnabled: $e) { success } }"
        d1, _ = run(core._graphql(q, {"u": "42", "e": True}))
        d2, _ = run(core._graphql(q, {"u": "42", "e": True}))
        assert calls["n"] == 2, "mutations must always reach the server - no 60s stale toggle"
        assert d1 == d2

    def test_cache_bounded_and_clearable(self):
        def handler(request):
            return httpx.Response(200, json={"n": 1})

        install(handler)
        for i in range(core._CACHE_MAX_ENTRIES + 10):
            run(core._api("GET", f"/v1/games.json?page={i}", ttl=60))
        assert len(core._CACHE) <= core._CACHE_MAX_ENTRIES
        core._clear_cache()
        assert not core._CACHE


class TestNoCacheCollisions:
    def test_same_path_different_params_not_colliding(self):
        payloads = {"/v1/games/skyrim/mods/1.json": {"modId": 1}, "/v1/games/skyrim/mods/2.json": {"modId": 2}}

        def handler(request):
            return httpx.Response(200, json=payloads[request.url.path])

        install(handler)
        p1, _ = run(core._api("GET", "/v1/games/skyrim/mods/1.json"))
        p2, _ = run(core._api("GET", "/v1/games/skyrim/mods/2.json"))
        assert p1 == {"modId": 1}
        assert p2 == {"modId": 2}
