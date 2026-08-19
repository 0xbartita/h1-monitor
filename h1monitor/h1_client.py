from __future__ import annotations

import asyncio

import httpx

from h1monitor.models import Snapshot, Program, Scope

_MAX_TRIES = 5
_MAX_BACKOFF = 30.0

# Per-program scope calls hit a hidden token-bucket: bursting trips it instantly,
# but steady sequential requests are fine (measured: 80 in a row @0.35s gap, zero
# 429s). The endpoint is slow (~2s/request), so a full private sweep takes minutes
# — that's the price of not rate-limiting. Pacing is adaptive: back off on 429,
# ease back toward the floor on clean successes.
_SCOPE_BASE_GAP = 0.35
_SCOPE_MAX_GAP = 5.0
_SCOPE_MAX_TRIES = 6


def _parse_program_item(item: dict) -> Program:
    a = item.get("attributes", {})
    return Program(
        a.get("handle"), a.get("name"), a.get("submission_state"),
        a.get("offers_bounties"), a.get("currency"), a.get("policy"), {},
    )


def _parse_scope_item(item: dict) -> Scope:
    a = item.get("attributes", {})
    return Scope(
        a.get("asset_type"), a.get("asset_identifier"),
        bool(a.get("eligible_for_bounty")), bool(a.get("eligible_for_submission")),
        a.get("max_severity"), a.get("instruction"),
        a.get("confidentiality_requirement"), a.get("integrity_requirement"),
        a.get("availability_requirement"), a.get("updated_at"), a.get("reference"),
    )


class H1Client:
    def __init__(
        self,
        username: str,
        token: str,
        base_url: str = "https://api.hackerone.com/v1",
        transport=None,
        scope_delay: float = _SCOPE_BASE_GAP,
    ):
        self._client = httpx.AsyncClient(
            auth=(username, token), base_url=base_url, transport=transport,
            timeout=30.0, headers={"Accept": "application/json"},
        )
        self._scope_gap = scope_delay   # floor / base pace between scope calls
        self._delay = scope_delay       # current (adaptive) pace

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, url: str) -> dict:
        resp = None
        for attempt in range(_MAX_TRIES):
            resp = await self._client.get(url)
            if resp.status_code == 429 and attempt < _MAX_TRIES - 1:
                # Rate limited — wait out HackerOne's Retry-After (capped so a
                # single hostile header can't wedge the whole scan).
                wait = min(float(resp.headers.get("Retry-After", "2")), _MAX_BACKOFF)
                await asyncio.sleep(wait)
                continue
            if resp.status_code >= 500 and attempt < _MAX_TRIES - 1:
                await asyncio.sleep(min(2**attempt, _MAX_BACKOFF))
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()
        return resp.json()

    async def _paginate(self, first_url: str) -> list[dict]:
        items: list[dict] = []
        url: str | None = first_url
        while url:
            body = await self._get(url)
            items.extend(body.get("data", []))
            url = (body.get("links") or {}).get("next")
        return items

    async def _get_scope_page(self, url: str) -> dict:
        """One scope page, self-throttled. Sleeps the adaptive gap before each
        request, backs off + slows down on 429, retries transient timeouts/5xx,
        and eases the pace back toward the floor after a clean response."""
        resp = None
        for attempt in range(_SCOPE_MAX_TRIES):
            if self._delay:
                await asyncio.sleep(self._delay)
            try:
                resp = await self._client.get(url)
            except httpx.TimeoutException:
                if self._scope_gap:
                    await asyncio.sleep(min(2**attempt, _MAX_BACKOFF))
                continue
            if resp.status_code == 429:
                # Slow the whole sweep down, wait, retry.
                self._delay = min(self._delay * 1.5 + 0.2, _SCOPE_MAX_GAP)
                if self._scope_gap:
                    wait = min(float(resp.headers.get("Retry-After", "5")), _MAX_BACKOFF)
                    await asyncio.sleep(wait)
                continue
            if resp.status_code >= 500:
                if self._scope_gap:
                    await asyncio.sleep(min(2**attempt, _MAX_BACKOFF))
                continue
            resp.raise_for_status()
            self._delay = max(self._delay * 0.9, self._scope_gap)
            return resp.json()
        if resp is None:
            raise RuntimeError(f"scope fetch failed (no response): {url}")
        resp.raise_for_status()
        return resp.json()

    async def _fetch_scopes(self, handle: str) -> list[dict]:
        items: list[dict] = []
        url: str | None = f"/hackers/programs/{handle}/structured_scopes?page[size]=100"
        while url:
            body = await self._get_scope_page(url)
            items.extend(body.get("data", []))
            url = (body.get("links") or {}).get("next")
        return items

    async def fetch_private_snapshot(
        self,
        previous: Snapshot | None = None,
        scope_handles: set[str] | None = None,
    ) -> Snapshot:
        """Fetch the operator's PRIVATE programs (state != "public_mode").

        Program-level fields (name/state/bounties) come from the fast list for
        ALL private programs. Detailed scopes are fetched per program
        SEQUENTIALLY and self-throttled — HackerOne's scope endpoint trips a
        hidden rate-limiter when hit in bursts, so a full sweep of hundreds of
        private programs takes minutes but never rate-limits.

        `scope_handles` selects WHICH programs get deep-scanned for scopes:
          * None  → scan every private program (the default).
          * a set → scan only those handles (an opt-in narrowing to cut API
                    load); the rest keep their previous scopes.
          * empty set → scan none.
        Programs that aren't scanned keep their previous scopes (or none)."""
        progs: list[Program] = []
        for item in await self._paginate("/hackers/programs?page[size]=100"):
            attrs = item.get("attributes", {})
            if attrs.get("state") == "public_mode":
                continue
            prog = _parse_program_item(item)
            if not prog.handle:
                continue
            prog.started_accepting_at = attrs.get("started_accepting_at")
            progs.append(prog)

        for prog in progs:  # sequential — no bursts, so the limiter stays happy
            prev = previous.programs.get(prog.handle) if previous else None
            scan = scope_handles is None or prog.handle in scope_handles
            if not scan:
                if prev is not None:
                    prog.scopes = prev.scopes
                continue
            try:
                items = await self._fetch_scopes(prog.handle)
                prog.scopes = {s.key: s for s in map(_parse_scope_item, items)}
            except Exception:  # noqa: BLE001 — one program must not fail the cycle
                if prev is not None:
                    prog.scopes = prev.scopes

        return Snapshot({p.handle: p for p in progs})
