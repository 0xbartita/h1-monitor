from __future__ import annotations

import httpx

from h1monitor.models import DirectoryProgram

DIRECTORY_QUERY = """
query DirectoryQuery($cursor: String) {
  teams(first: 50, after: $cursor,
        secure_order_by: {started_accepting_at: {_direction: DESC}},
        where: {_and: [{submission_state: {_eq: open}},
                       {_not: {external_program: {_is_null: false}}}]}) {
    pageInfo { hasNextPage endCursor }
    edges { node { handle name offers_bounties submission_state
                   started_accepting_at url } }
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
        self._client = httpx.AsyncClient(base_url=base, transport=transport, timeout=30.0)
        self._cookie = cookie
        self._csrf: str | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _bootstrap(self) -> None:
        if self._cookie and self._csrf:
            return
        r = await self._client.get("/directory/programs")
        if self._cookie is None:
            self._cookie = r.headers.get("set-cookie", "")
        self._csrf = r.headers.get("x-csrf-token", "")

    async def fetch_all(self) -> list[DirectoryProgram]:
        await self._bootstrap()
        headers = {"Content-Type": "application/json"}
        if self._cookie:
            headers["Cookie"] = self._cookie
        if self._csrf:
            headers["X-Csrf-Token"] = self._csrf
        out: list[DirectoryProgram] = []
        cursor: str | None = None
        while True:
            resp = await self._client.post(
                "/graphql",
                headers=headers,
                json={"query": DIRECTORY_QUERY, "variables": {"cursor": cursor}},
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
            cursor = page.get("endCursor")
            if not cursor:
                break
        return out
