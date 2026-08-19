import httpx
import pytest

from h1monitor.directory_client import DirectoryClient, _parse_team_node


def test_parse_team_node_defensive():
    node = {
        "handle": "vercel", "name": "Vercel", "offers_bounties": True,
        "submission_state": "open", "started_accepting_at": "2026-08-18",
        "url": "https://hackerone.com/vercel",
    }
    dp = _parse_team_node(node)
    assert dp.handle == "vercel" and dp.started_accepting_at == "2026-08-18"


def test_parse_team_node_missing_fields():
    dp = _parse_team_node({"handle": "x"})
    assert dp.handle == "x" and dp.offers_bounties is False and dp.url is None


@pytest.mark.asyncio
async def test_fetch_all_paginates():
    pages = [
        {
            "data": {
                "teams": {
                    "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                    "edges": [
                        {
                            "node": {
                                "handle": "a", "name": "A", "offers_bounties": True,
                                "submission_state": "open",
                                "started_accepting_at": "d", "url": "u",
                            }
                        }
                    ],
                }
            }
        },
        {
            "data": {
                "teams": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "edges": [
                        {
                            "node": {
                                "handle": "b", "name": "B", "offers_bounties": False,
                                "submission_state": "open",
                                "started_accepting_at": "d", "url": "u",
                            }
                        }
                    ],
                }
            }
        },
    ]
    calls = {"n": 0}

    seen = {}

    def handler(request):
        if request.url.path == "/directory/programs":
            return httpx.Response(
                200,
                text='<html><head><meta name="csrf-token" content="tok123"></head></html>',
            )
        # capture the CSRF header the client sends on the GraphQL POST
        seen["csrf"] = request.headers.get("x-csrf-token")
        i = calls["n"]
        calls["n"] += 1
        return httpx.Response(200, json=pages[i])

    c = DirectoryClient(transport=httpx.MockTransport(handler))
    progs = await c.fetch_all()
    await c.aclose()
    assert [p.handle for p in progs] == ["a", "b"]
    assert seen["csrf"] == "tok123"  # CSRF read from the page meta tag, sent on POST
