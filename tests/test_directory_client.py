import httpx
import pytest

from h1monitor.directory_client import DirectoryClient, _program_from_node


def test_program_from_node_with_scopes():
    node = {
        "handle": "vercel", "name": "Vercel", "offers_bounties": True,
        "submission_state": "open", "started_accepting_at": "2026-08-18T14:38:59Z",
        "structured_scopes": {"edges": [
            {"node": {"asset_identifier": "vercel.com", "asset_type": "URL",
                      "eligible_for_bounty": True, "eligible_for_submission": True,
                      "max_severity": "critical", "instruction": "go"}},
        ]},
    }
    p = _program_from_node(node)
    assert p.handle == "vercel"
    assert p.started_accepting_at == "2026-08-18T14:38:59Z"
    assert p.scopes["URL:vercel.com"].max_severity == "critical"


def test_program_from_node_missing_scopes():
    p = _program_from_node({"handle": "x", "name": "X", "offers_bounties": False,
                            "submission_state": "open"})
    assert p.handle == "x" and p.scopes == {} and p.offers_bounties is False


@pytest.mark.asyncio
async def test_fetch_public_snapshot_paginates():
    def _page(handle, has_next, cursor):
        return {"data": {"teams": {
            "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
            "edges": [{"node": {
                "handle": handle, "name": handle.title(), "offers_bounties": True,
                "submission_state": "open", "started_accepting_at": "d",
                "structured_scopes": {"edges": [
                    {"node": {"asset_identifier": f"{handle}.com", "asset_type": "URL",
                              "eligible_for_bounty": True, "eligible_for_submission": True,
                              "max_severity": "high", "instruction": None}}]},
            }}],
        }}}

    pages = [_page("a", True, "c1"), _page("b", False, None)]
    calls = {"n": 0}
    seen = {}

    def handler(request):
        if request.url.path == "/directory/programs":
            return httpx.Response(
                200, text='<meta name="csrf-token" content="tok123">')
        seen["csrf"] = request.headers.get("x-csrf-token")
        i = calls["n"]
        calls["n"] += 1
        return httpx.Response(200, json=pages[i])

    c = DirectoryClient(transport=httpx.MockTransport(handler))
    snap = await c.fetch_public_snapshot()
    await c.aclose()
    assert set(snap.programs) == {"a", "b"}
    assert snap.programs["a"].scopes["URL:a.com"].max_severity == "high"
    assert seen["csrf"] == "tok123"


@pytest.mark.asyncio
async def test_fetch_public_snapshot_retries_transient_network_error():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadError("transient blip", request=request)
        if request.url.path == "/directory/programs":
            return httpx.Response(200, text='<meta name="csrf-token" content="t">')
        return httpx.Response(200, json={"data": {"teams": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "edges": [{"node": {
                "handle": "acme", "name": "Acme", "offers_bounties": True,
                "submission_state": "open", "started_accepting_at": "d",
                "structured_scopes": {"edges": []}}}]}}})

    c = DirectoryClient(transport=httpx.MockTransport(handler), retry_delay=0)
    snap = await c.fetch_public_snapshot()
    await c.aclose()
    assert "acme" in snap.programs   # recovered instead of failing the cycle
    assert calls["n"] >= 2           # first attempt raised, retry succeeded


@pytest.mark.asyncio
async def test_fetch_public_snapshot_gives_up_after_persistent_errors():
    def handler(request):
        raise httpx.ReadError("down", request=request)

    c = DirectoryClient(transport=httpx.MockTransport(handler), retry_delay=0)
    with pytest.raises(httpx.TransportError):
        await c.fetch_public_snapshot()
    await c.aclose()


@pytest.mark.asyncio
async def test_fetch_public_snapshot_raises_on_graphql_errors():
    def handler(request):
        if request.url.path == "/directory/programs":
            return httpx.Response(200, text='<meta name="csrf-token" content="t">')
        return httpx.Response(200, json={"errors": [{"message": "bad"}]})

    c = DirectoryClient(transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError):
        await c.fetch_public_snapshot()
    await c.aclose()


@pytest.mark.asyncio
async def test_null_edges_does_not_crash_public_fetch():
    def handler(request):
        if request.url.path == "/directory/programs":
            return httpx.Response(200, text='<meta name="csrf-token" content="t">')
        return httpx.Response(200, json={"data": {"teams": {
            "edges": None, "pageInfo": {"hasNextPage": False, "endCursor": None}}}})
    c = DirectoryClient(transport=httpx.MockTransport(handler), retry_delay=0)
    snap = await c.fetch_public_snapshot()
    await c.aclose()
    assert snap.programs == {}   # was: TypeError from `for edge in None`
