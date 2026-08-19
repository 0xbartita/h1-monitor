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
_RETRY_AFTER_FLOOR = 1.0  # never wait less than this on a 429 (don't hammer)


def _retry_after(headers, default: float, floor: float = _RETRY_AFTER_FLOOR) -> float:
    """Parse a 429/503 Retry-After into a safe sleep. A non-numeric value (e.g.
    an RFC-7231 HTTP-date) or a missing header falls back to `default` instead of
    crashing; the result is clamped to [floor, _MAX_BACKOFF] so a hostile 0 or
    negative value can never cause a tight retry storm against HackerOne."""
    raw = headers.get("Retry-After")
    try:
        wait = float(raw) if raw is not None else default
    except (TypeError, ValueError):
        wait = default
    return min(max(wait, floor), _MAX_BACKOFF)


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
        retry_delay: float = 0.5,
    ):
        self._client = httpx.AsyncClient(
            auth=(username, token), base_url=base_url, transport=transport,
            timeout=30.0, headers={"Accept": "application/json"},
        )
        self._scope_gap = scope_delay   # floor / base pace between scope calls
        self._delay = scope_delay       # current (adaptive) pace
        self._retry_delay = retry_delay  # backoff base for transient-error retries

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, url: str) -> dict:
        resp = None
        for attempt in range(_MAX_TRIES):
            last = attempt == _MAX_TRIES - 1
            try:
                resp = await self._client.get(url)
            except httpx.TransportError:
                # Transient network blip (ReadTimeout, ConnectError, ReadError…).
                # A single one must not abort a multi-minute sweep — retry with
                # backoff, and only surface it if it persists across every try.
                if last:
                    raise
                await asyncio.sleep(min(self._retry_delay * 2**attempt, _MAX_BACKOFF))
                continue
            if resp.status_code == 429 and not last:
                # Rate limited — wait out HackerOne's Retry-After (floored so a
                # hostile 0 can't hammer, capped so a huge one can't wedge).
                await asyncio.sleep(_retry_after(resp.headers, default=2))
                continue
            if resp.status_code >= 500 and not last:
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
            items.extend(body.get("data") or [])
            url = (body.get("links") or {}).get("next")
        return items

    async def _get_scope_page(self, url: str) -> dict:
        """One scope page, self-throttled. Sleeps the adaptive gap before each
        request, backs off + slows down on 429, retries transient network errors
        (timeouts, resets, …) and 5xx, and eases the pace back toward the floor
        after a clean response."""
        resp = None
        for attempt in range(_SCOPE_MAX_TRIES):
            if self._delay:
                await asyncio.sleep(self._delay)
            try:
                resp = await self._client.get(url)
            except httpx.TransportError:
                if self._scope_gap:
                    await asyncio.sleep(min(2**attempt, _MAX_BACKOFF))
                continue
            if resp.status_code == 429:
                # Slow the whole sweep down, wait, retry.
                self._delay = min(self._delay * 1.5 + 0.2, _SCOPE_MAX_GAP)
                if self._scope_gap:
                    await asyncio.sleep(_retry_after(resp.headers, default=5))
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
            items.extend(body.get("data") or [])
            url = (body.get("links") or {}).get("next")
        return items

    async def fetch_private_snapshot(
        self,
        previous: Snapshot | None = None,
    ) -> Snapshot:
        """Fetch the operator's PRIVATE programs (state != "public_mode"), with
        every program's scopes.

        Program-level fields (name/state/bounties) come from the fast list.
        Detailed scopes are fetched per program SEQUENTIALLY and self-throttled
        — HackerOne's scope endpoint trips a hidden rate-limiter when hit in
        bursts, so a full sweep of hundreds of private programs takes minutes but
        never rate-limits. A program whose scope fetch fails keeps its previous
        scopes so one hiccup doesn't wipe the cycle."""
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
            try:
                items = await self._fetch_scopes(prog.handle)
                prog.scopes = {s.key: s for s in map(_parse_scope_item, items)}
            except Exception:  # noqa: BLE001 — one program must not fail the cycle
                if prev is not None:
                    prog.scopes = prev.scopes

        return Snapshot({p.handle: p for p in progs})
