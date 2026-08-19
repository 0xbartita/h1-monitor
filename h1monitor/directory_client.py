from __future__ import annotations

import re

import httpx

from h1monitor.models import Snapshot, Program, Scope

DIRECTORY_PAGE = "/directory/programs"
GRAPHQL_URL = "/graphql"
_CSRF_RE = re.compile(r'name="csrf-token"\s+content="([^"]+)"')
_UA = "Mozilla/5.0 (X11; Linux x86_64) h1monitor/0.1"

# Bulk query: every open public program plus its in-scope structured_scopes,
# in one paginated pass (no API key). Mirrors HackerOne's public directory.
PUBLIC_QUERY = """
query($after: String) {
  teams(first: 25, after: $after,
        secure_order_by: {started_accepting_at: {_direction: DESC}},
        where: {_and: [{_or: [{submission_state: {_eq: open}}, {external_program: {}}]},
                       {_not: {external_program: {}}},
                       {_or: [{_and: [{state: {_neq: sandboxed}},
                                      {state: {_neq: soft_launched}}]},
                              {external_program: {}}]}]}) {
    pageInfo { hasNextPage endCursor }
    edges { node {
      handle name offers_bounties submission_state started_accepting_at
      structured_scopes(first: 200, archived: false, eligible_for_submission: true) {
        edges { node { asset_identifier asset_type eligible_for_bounty
                       eligible_for_submission max_severity instruction } }
      }
    } }
  }
}
""".strip()


def _scope_from_node(n: dict) -> Scope:
    return Scope(
        n.get("asset_type"), n.get("asset_identifier"),
        bool(n.get("eligible_for_bounty")), bool(n.get("eligible_for_submission")),
        n.get("max_severity"), n.get("instruction"),
        None, None, None, None, None,
    )


def _program_from_node(node: dict) -> Program:
    scopes: dict[str, Scope] = {}
    for edge in (node.get("structured_scopes") or {}).get("edges", []):
        sn = edge.get("node") or {}
        if sn.get("asset_identifier") is not None and sn.get("asset_type"):
            s = _scope_from_node(sn)
            scopes[s.key] = s
    return Program(
        handle=node.get("handle"),
        name=node.get("name") or node.get("handle") or "",
        submission_state=node.get("submission_state"),
        offers_bounties=bool(node.get("offers_bounties")),
        currency=None,
        policy=None,
        scopes=scopes,
        started_accepting_at=node.get("started_accepting_at"),
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
            base_url=base, transport=transport, timeout=45.0, headers=headers,
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _fetch_csrf(self) -> str:
        r = await self._client.get(DIRECTORY_PAGE)
        m = _CSRF_RE.search(r.text)
        return m.group(1) if m else ""

    async def fetch_public_snapshot(self) -> Snapshot:
        csrf = await self._fetch_csrf()
        headers = {"Content-Type": "application/json"}
        if csrf:
            headers["X-Csrf-Token"] = csrf
        programs: dict[str, Program] = {}
        after: str | None = None
        while True:
            resp = await self._client.post(
                GRAPHQL_URL,
                headers=headers,
                json={"query": PUBLIC_QUERY, "variables": {"after": after}},
            )
            resp.raise_for_status()
            body = resp.json()
            if body.get("errors"):
                raise RuntimeError(f"directory graphql errors: {body['errors']}")
            teams = (body.get("data") or {}).get("teams") or {}
            for edge in teams.get("edges", []):
                node = edge.get("node") or {}
                if node.get("handle"):
                    prog = _program_from_node(node)
                    programs[prog.handle] = prog
            page = teams.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                break
            after = page.get("endCursor")
            if not after:
                break
        return Snapshot(programs)
