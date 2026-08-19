import httpx
import pytest

from h1monitor.h1_client import H1Client, _parse_program_item, _parse_scope_item


def _prog_item(handle, state="private_mode"):
    return {"id": "1", "attributes": {
        "handle": handle, "name": handle.title(), "submission_state": "open",
        "offers_bounties": True, "currency": "USD", "policy": "p", "state": state}}


def _scopes_body():
    return {"data": [{"id": "9", "attributes": {
        "asset_type": "URL", "asset_identifier": "a.com", "eligible_for_bounty": True,
        "eligible_for_submission": True, "max_severity": "high", "updated_at": "t"}}],
        "links": {}}


def test_parse_program_item():
    p = _parse_program_item(_prog_item("acme"))
    assert (p.handle, p.name, p.offers_bounties) == ("acme", "Acme", True)


def test_parse_scope_item():
    item = {"id": "9", "attributes": {
        "asset_type": "URL", "asset_identifier": "a.com", "eligible_for_bounty": True,
        "eligible_for_submission": True, "max_severity": "high", "instruction": "x",
        "updated_at": "t"}}
    s = _parse_scope_item(item)
    assert s.key == "URL:a.com" and s.max_severity == "high"


@pytest.mark.asyncio
async def test_fetch_private_snapshot_includes_private_with_scopes():
    def handler(request):
        url = str(request.url)
        if url.endswith("/hackers/programs"):
            return httpx.Response(200, json={"data": [_prog_item("acme")], "links": {}})
        if "structured_scopes" in url:
            return httpx.Response(200, json=_scopes_body())
        return httpx.Response(404)

    client = H1Client("id", "tok", transport=httpx.MockTransport(handler))
    snap = await client.fetch_private_snapshot()
    await client.aclose()
    assert "acme" in snap.programs
    assert snap.programs["acme"].scopes["URL:a.com"].max_severity == "high"


@pytest.mark.asyncio
async def test_public_mode_programs_are_excluded():
    def handler(request):
        url = str(request.url)
        if url.endswith("/hackers/programs"):
            return httpx.Response(200, json={"data": [
                _prog_item("pub", state="public_mode"),
                _prog_item("priv", state="private_mode"),
            ], "links": {}})
        if "structured_scopes" in url:
            return httpx.Response(200, json=_scopes_body())
        return httpx.Response(404)

    client = H1Client("id", "tok", transport=httpx.MockTransport(handler))
    snap = await client.fetch_private_snapshot()
    await client.aclose()
    assert set(snap.programs) == {"priv"}  # public_mode excluded


@pytest.mark.asyncio
async def test_per_program_error_reuses_previous():
    from h1monitor.models import Snapshot, Program, Scope

    prev = Snapshot({"priv": Program("priv", "Priv", "open", True, "USD", "p",
        {"URL:a.com": Scope("URL", "a.com", True, True, "low",
                            None, None, None, None, "t")})})

    def handler(request):
        url = str(request.url)
        if url.endswith("/hackers/programs"):
            return httpx.Response(200, json={"data": [_prog_item("priv")], "links": {}})
        return httpx.Response(500)  # scopes fail

    client = H1Client("id", "tok", transport=httpx.MockTransport(handler))
    snap = await client.fetch_private_snapshot(previous=prev)
    await client.aclose()
    assert snap.programs["priv"].scopes["URL:a.com"].max_severity == "low"
