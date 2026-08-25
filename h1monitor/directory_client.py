from __future__ import annotations

import asyncio
import logging
import re

import httpx

from h1monitor.models import Snapshot, Program, Scope

log = logging.getLogger("h1monitor")

DIRECTORY_PAGE = "/directory/programs"
GRAPHQL_URL = "/graphql"
_MAX_TRIES = 4
_BASE_BACKOFF = 0.5
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
        pageInfo { hasNextPage }
        edges { node { asset_identifier asset_type eligible_for_bounty
                       eligible_for_submission max_severity instruction } }
      }
    } }
  }
}
""".strip()

# The bulk query above takes only the first 200 scopes per program, and about
# 4% of the directory has more than that (John Deere alone has 2,003). Those
# programs need a second pass: this walks one team's scope list to the end.
TEAM_SCOPES_QUERY = """
query($handle: String!, $after: String) {
  team(handle: $handle) {
    structured_scopes(first: 200, after: $after, archived: false,
                      eligible_for_submission: true) {
      pageInfo { hasNextPage endCursor }
      edges { node { asset_identifier asset_type eligible_for_bounty
                     eligible_for_submission max_severity instruction } }
    }
  }
}
""".strip()

_SCOPE_PAGE_PAUSE = 0.2   # breathing room between top-up pages; H1 429s on bursts
# The biggest program on the platform carries ~9,800 in-scope assets, so this is
# ~2.5x headroom. It exists to stop a paging bug spinning forever, not to cap
# real programs — running into it raises rather than returning a partial list.
_MAX_SCOPE_PAGES = 120    # 24,000 assets


def _scope_from_node(n: dict) -> Scope:
    return Scope(
        n.get("asset_type"), n.get("asset_identifier"),
        bool(n.get("eligible_for_bounty")), bool(n.get("eligible_for_submission")),
        n.get("max_severity"), n.get("instruction"),
        None, None, None, None, None,
    )


def _scopes_were_truncated(node: dict) -> bool:
    """True when the bulk query returned only part of this program's scopes."""
    ss = node.get("structured_scopes") or {}
    return bool((ss.get("pageInfo") or {}).get("hasNextPage"))


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
        retry_delay: float = _BASE_BACKOFF,
        max_tries: int = _MAX_TRIES,
    ):
        headers = {"User-Agent": _UA}
        if cookie:
            headers["Cookie"] = cookie
        self._client = httpx.AsyncClient(
            base_url=base, transport=transport, timeout=45.0, headers=headers,
            follow_redirects=True,
        )
        self._retry_delay = retry_delay
        self._max_tries = max_tries

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, url: str, **kw) -> httpx.Response:
        """One HTTP call, retrying transient network blips and 5xx with backoff.
        A single flaky read shouldn't abort a whole ~18-page directory sweep."""
        for attempt in range(self._max_tries):
            last = attempt == self._max_tries - 1
            try:
                resp = await self._client.request(method, url, **kw)
            except httpx.TransportError:
                if last:
                    raise
            else:
                if resp.status_code < 500 or last:
                    return resp
            await asyncio.sleep(self._retry_delay * (2 ** attempt))
        raise RuntimeError("unreachable")  # loop always returns or raises

    async def _fetch_csrf(self) -> str:
        r = await self._request("GET", DIRECTORY_PAGE)
        m = _CSRF_RE.search(r.text)
        return m.group(1) if m else ""

    async def _fetch_all_scopes(self, handle: str, headers: dict) -> dict[str, Scope]:
        """Every in-scope asset for one program, following the cursor to the end."""
        scopes: dict[str, Scope] = {}
        after: str | None = None
        for _ in range(_MAX_SCOPE_PAGES):
            resp = await self._request(
                "POST", GRAPHQL_URL, headers=headers,
                json={"query": TEAM_SCOPES_QUERY,
                      "variables": {"handle": handle, "after": after}},
            )
            resp.raise_for_status()
            body = resp.json()
            if body.get("errors"):
                raise RuntimeError(f"scope graphql errors: {body['errors']}")
            ss = (((body.get("data") or {}).get("team") or {})
                  .get("structured_scopes") or {})
            for edge in ss.get("edges") or []:
                sn = edge.get("node") or {}
                if sn.get("asset_identifier") is not None and sn.get("asset_type"):
                    s = _scope_from_node(sn)
                    scopes[s.key] = s
            page = ss.get("pageInfo") or {}
            after = page.get("endCursor")
            if not page.get("hasNextPage") or not after:
                return scopes
            await asyncio.sleep(_SCOPE_PAGE_PAUSE)
        # Ran out of pages with more still to come. Returning what we have would
        # look like a complete list and report every asset beyond it as removed,
        # so treat it as a failed read and let the caller fall back.
        raise RuntimeError(
            f"{handle}: more than {_MAX_SCOPE_PAGES} pages of scopes"
        )

    async def fetch_public_snapshot(self, previous: Snapshot | None = None) -> Snapshot:
        csrf = await self._fetch_csrf()
        headers = {"Content-Type": "application/json"}
        if csrf:
            headers["X-Csrf-Token"] = csrf
        programs: dict[str, Program] = {}
        truncated: list[str] = []
        after: str | None = None
        while True:
            resp = await self._request(
                "POST", GRAPHQL_URL,
                headers=headers,
                json={"query": PUBLIC_QUERY, "variables": {"after": after}},
            )
            resp.raise_for_status()
            body = resp.json()
            if body.get("errors"):
                raise RuntimeError(f"directory graphql errors: {body['errors']}")
            teams = (body.get("data") or {}).get("teams") or {}
            for edge in teams.get("edges") or []:
                node = edge.get("node") or {}
                if node.get("handle"):
                    prog = _program_from_node(node)
                    programs[prog.handle] = prog
                    if _scopes_were_truncated(node):
                        truncated.append(prog.handle)
            page = teams.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                break
            after = page.get("endCursor")
            if not after:
                break

        # Second pass over the programs whose scope list was cut short. Without
        # it, a new asset on a large program can never be noticed, and archiving
        # one pulls an unrelated asset into view and reports it as newly added.
        for handle in truncated:
            try:
                programs[handle].scopes = await self._fetch_all_scopes(handle, headers)
            except Exception:  # noqa: BLE001 — see below
                # Diffing a truncated list against a complete one would report
                # every asset past position 200 as removed. Carry the previous
                # cycle's scopes instead and stay quiet about this program until
                # the top-up succeeds; one program's bad page must not poison a
                # whole sweep.
                prev = previous.programs.get(handle) if previous else None
                if prev is not None:
                    programs[handle].scopes = dict(prev.scopes)
                else:
                    programs[handle].scopes = {}
                log.warning(
                    "Couldn't read all scopes for %s; keeping the previous set",
                    handle, exc_info=True,
                )
        return Snapshot(programs)
