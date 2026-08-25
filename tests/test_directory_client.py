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


# --- the 200-scope cap: large programs need a second, paginated pass ---------

def _team_page(handle, assets, has_next=False, cursor=None, truncated=False):
    """One bulk-sweep page carrying a single program."""
    return {"data": {"teams": {
        "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
        "edges": [{"node": {
            "handle": handle, "name": handle.title(), "offers_bounties": True,
            "submission_state": "open", "started_accepting_at": "d",
            "structured_scopes": {
                "pageInfo": {"hasNextPage": truncated},
                "edges": [{"node": {
                    "asset_identifier": a, "asset_type": "URL",
                    "eligible_for_bounty": True, "eligible_for_submission": True,
                    "max_severity": "high", "instruction": None}} for a in assets],
            },
        }}],
    }}}


def _scope_page(assets, has_next=False, cursor=None):
    """One page of the per-program scope query."""
    return {"data": {"team": {"structured_scopes": {
        "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
        "edges": [{"node": {
            "asset_identifier": a, "asset_type": "URL",
            "eligible_for_bounty": True, "eligible_for_submission": True,
            "max_severity": "high", "instruction": None}} for a in assets],
    }}}}


def _routed(bulk_pages, scope_pages, fail_scopes=False):
    """A transport that serves the bulk sweep and the per-program top-up, and
    records which queries were asked for."""
    state = {"bulk": 0, "scope": 0, "scope_calls": 0}

    def handler(request):
        if request.url.path == "/directory/programs":
            return httpx.Response(200, text='<meta name="csrf-token" content="t">')
        body = request.content.decode()
        if "team(handle:" in body:
            state["scope_calls"] += 1
            if fail_scopes:
                return httpx.Response(500)
            i = min(state["scope"], len(scope_pages) - 1)
            state["scope"] += 1
            return httpx.Response(200, json=scope_pages[i])
        i = min(state["bulk"], len(bulk_pages) - 1)
        state["bulk"] += 1
        return httpx.Response(200, json=bulk_pages[i])

    return handler, state


@pytest.mark.asyncio
async def test_truncated_program_is_topped_up_with_every_scope():
    # The bulk query caps scopes per program. A program that hit the cap must be
    # re-read to the end, or a new asset on it could never be noticed.
    handler, state = _routed(
        [_team_page("big", ["a.com"], truncated=True)],
        [_scope_page(["a.com", "b.com"], has_next=True, cursor="s1"),
         _scope_page(["c.com"])],
    )
    c = DirectoryClient(transport=httpx.MockTransport(handler), retry_delay=0)
    snap = await c.fetch_public_snapshot()
    await c.aclose()
    assert set(snap.programs["big"].scopes) == {"URL:a.com", "URL:b.com", "URL:c.com"}
    assert state["scope_calls"] == 2          # followed the cursor


@pytest.mark.asyncio
async def test_untruncated_program_costs_no_extra_request():
    handler, state = _routed([_team_page("small", ["a.com"], truncated=False)], [])
    c = DirectoryClient(transport=httpx.MockTransport(handler), retry_delay=0)
    snap = await c.fetch_public_snapshot()
    await c.aclose()
    assert set(snap.programs["small"].scopes) == {"URL:a.com"}
    assert state["scope_calls"] == 0


@pytest.mark.asyncio
async def test_failed_top_up_keeps_the_previous_scopes():
    # Diffing a truncated list against a complete one would report every asset
    # past the cap as removed. Carry the last known set instead.
    from h1monitor.models import Snapshot, Program, Scope

    def _sc(a):
        return Scope("URL", a, True, True, "high", None, None, None, None, None)

    previous = Snapshot({"big": Program(
        "big", "Big", "open", True, None, None,
        {"URL:a.com": _sc("a.com"), "URL:z.com": _sc("z.com")}, "d")})

    handler, state = _routed(
        [_team_page("big", ["a.com"], truncated=True)], [], fail_scopes=True)
    c = DirectoryClient(transport=httpx.MockTransport(handler),
                        retry_delay=0, max_tries=1)
    snap = await c.fetch_public_snapshot(previous)
    await c.aclose()
    assert set(snap.programs["big"].scopes) == {"URL:a.com", "URL:z.com"}


@pytest.mark.asyncio
async def test_failed_top_up_with_no_baseline_reports_no_scopes():
    # Nothing to fall back on: better to hold an empty set (and stay silent) than
    # to publish a truncated one as if it were complete.
    handler, state = _routed(
        [_team_page("big", ["a.com"], truncated=True)], [], fail_scopes=True)
    c = DirectoryClient(transport=httpx.MockTransport(handler),
                        retry_delay=0, max_tries=1)
    snap = await c.fetch_public_snapshot()
    await c.aclose()
    assert snap.programs["big"].scopes == {}


@pytest.mark.asyncio
async def test_hitting_the_page_limit_is_treated_as_a_failed_read(monkeypatch):
    # A scope list longer than the safety limit must not come back looking whole —
    # that would report everything past the limit as removed.
    import h1monitor.directory_client as dc
    monkeypatch.setattr(dc, "_MAX_SCOPE_PAGES", 2)
    monkeypatch.setattr(dc, "_SCOPE_PAGE_PAUSE", 0)
    from h1monitor.models import Snapshot, Program, Scope

    previous = Snapshot({"big": Program(
        "big", "Big", "open", True, None, None,
        {"URL:kept.com": Scope("URL", "kept.com", True, True, None, None,
                               None, None, None, None)}, "d")})
    # Every page claims there is another one, so the limit is always reached.
    handler, state = _routed(
        [_team_page("big", ["a.com"], truncated=True)],
        [_scope_page(["a.com"], has_next=True, cursor="s1")],
    )
    c = dc.DirectoryClient(transport=httpx.MockTransport(handler), retry_delay=0)
    snap = await c.fetch_public_snapshot(previous)
    await c.aclose()
    assert state["scope_calls"] == 2                       # stopped at the limit
    assert set(snap.programs["big"].scopes) == {"URL:kept.com"}   # fell back
