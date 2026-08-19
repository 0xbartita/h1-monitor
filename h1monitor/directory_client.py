from __future__ import annotations

import re

import httpx

from h1monitor.models import DirectoryProgram

DIRECTORY_PAGE = "/directory/programs"
GRAPHQL_URL = "/graphql"
_CSRF_RE = re.compile(r'name="csrf-token"\s+content="([^"]+)"')
_UA = "Mozilla/5.0 (X11; Linux x86_64) h1monitor/0.1"

# Query shape mirrors HackerOne's public directory (as used by community tools).
# Variable is $after; results are ordered newest-first by started_accepting_at.
DIRECTORY_QUERY = """
query($after: String) {
  teams(first: 50, after: $after,
        secure_order_by: {started_accepting_at: {_direction: DESC}},
        where: {_and: [{_or: [{submission_state: {_eq: open}}, {external_program: {}}]},
                       {_not: {external_program: {}}},
                       {_or: [{_and: [{state: {_neq: sandboxed}},
                                      {state: {_neq: soft_launched}}]},
                              {external_program: {}}]}]}) {
    pageInfo { hasNextPage endCursor }
    edges { node { id handle name url submission_state offers_bounties
                   started_accepting_at } }
  }
}
""".strip()


def _parse_team_node(node: dict) -> DirectoryProgram:
    return DirectoryProgram(
        handle=node.get("handle"),
        name=node.get("name") or node.get("handle") or "",
        offers_bounties=bool(node.get("offers_bounties")),
        submission_state=node.get("submission_state"),
        started_accepting_at=node.get("started_accepting_at"),
        url=node.get("url"),
    )


class DirectoryClient:
    def __init__(
        self,
        cookie: str | None = None,
        base: str = "https://hackerone.com",
        transport=None,
    ):
        headers = {"User-Agent": _UA}
        if cookie:
            headers["Cookie"] = cookie
        self._client = httpx.AsyncClient(
            base_url=base, transport=transport, timeout=30.0, headers=headers,
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _fetch_csrf(self) -> str:
        """GET the directory page: httpx stores its session cookies in the jar
        automatically, and the CSRF token is read from the page's meta tag."""
        r = await self._client.get(DIRECTORY_PAGE)
        m = _CSRF_RE.search(r.text)
        return m.group(1) if m else ""

    async def fetch_all(self) -> list[DirectoryProgram]:
        csrf = await self._fetch_csrf()
        headers = {"Content-Type": "application/json"}
        if csrf:
            headers["X-Csrf-Token"] = csrf
        out: list[DirectoryProgram] = []
        after: str | None = None
        while True:
            resp = await self._client.post(
                GRAPHQL_URL,
                headers=headers,
                json={"query": DIRECTORY_QUERY, "variables": {"after": after}},
            )
            resp.raise_for_status()
            teams = (resp.json().get("data") or {}).get("teams") or {}
            for edge in teams.get("edges", []):
                node = edge.get("node") or {}
                if node.get("handle"):
                    out.append(_parse_team_node(node))
            page = teams.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                break
            after = page.get("endCursor")
            if not after:
                break
        return out
