from __future__ import annotations

import asyncio

import httpx

from h1monitor.models import Snapshot, Program, Scope

_MAX_TRIES = 3


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
    ):
        self._client = httpx.AsyncClient(
            auth=(username, token), base_url=base_url, transport=transport,
            timeout=30.0, headers={"Accept": "application/json"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, url: str) -> dict:
        resp = None
        for attempt in range(_MAX_TRIES):
            resp = await self._client.get(url)
            if resp.status_code == 429 and attempt < _MAX_TRIES - 1:
                await asyncio.sleep(float(resp.headers.get("Retry-After", "1")))
                continue
            if resp.status_code >= 500 and attempt < _MAX_TRIES - 1:
                await asyncio.sleep(2**attempt)
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

    async def fetch_snapshot(self, previous: Snapshot | None = None) -> Snapshot:
        programs: dict[str, Program] = {}
        for item in await self._paginate("/hackers/programs"):
            prog = _parse_program_item(item)
            if not prog.handle:
                continue
            try:
                scope_items = await self._paginate(
                    f"/hackers/programs/{prog.handle}/structured_scopes"
                )
                prog.scopes = {s.key: s for s in map(_parse_scope_item, scope_items)}
            except httpx.HTTPError:
                if previous and prog.handle in previous.programs:
                    prog.scopes = previous.programs[prog.handle].scopes
                else:
                    continue
            programs[prog.handle] = prog
        return Snapshot(programs)
