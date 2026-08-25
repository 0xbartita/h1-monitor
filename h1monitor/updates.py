from __future__ import annotations

import asyncio
import logging
import os
import re

import httpx

from h1monitor import __version__
from h1monitor.notifier import escape_html

log = logging.getLogger("h1monitor")

DEFAULT_REPO = "0xbartita/h1-monitor"
# Announcement channel. The hourly check already tells each copy about a new
# release; this is for people who'd rather hear it pushed than wait for their
# own instance to notice — and it still reaches them if their bot is stopped.
UPDATES_CHANNEL = "https://t.me/h1_monitor"
UPDATES_CHANNEL_HANDLE = "@h1_monitor"
_TIMEOUT = 10.0
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")


def repo_slug() -> str:
    return os.environ.get("H1MON_GITHUB_REPO") or DEFAULT_REPO


def _parse(tag) -> tuple[int, int, int] | None:
    if not tag:
        return None
    m = _VERSION.match(str(tag).strip())
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def is_newer(latest, current) -> bool:
    """True only when both tags parse and upstream is genuinely ahead. Compared
    as numbers, so 0.10.0 beats 0.9.0. Anything unparseable ('nightly', '') is
    treated as 'no update' — nagging someone toward an upgrade they don't need
    is worse than staying quiet."""
    a, b = _parse(latest), _parse(current)
    return bool(a and b and a > b)


def _display(tag) -> str:
    """'v0.2.0' and '0.2.0' both render as '0.2.0'."""
    return str(tag or "").strip().lstrip("vV")


class UpdateClient:
    """Reads the newest published version from GitHub's public API."""

    def __init__(
        self,
        slug: str | None = None,
        transport=None,
        base_url: str = "https://api.github.com",
    ):
        self._slug = slug or repo_slug()
        self._base = base_url.rstrip("/")
        self._http = httpx.AsyncClient(
            transport=transport,
            timeout=_TIMEOUT,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"h1monitor/{__version__}",
            },
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def latest_release(self) -> tuple[str, str] | None:
        """`(tag, release_url)` of the newest version, or None.

        Every failure path returns None rather than raising: an update check is
        a nicety, and GitHub being down, rate-limiting us (60/hr unauthenticated)
        or answering with an unexpected shape must never disturb monitoring."""
        try:
            r = await self._http.get(
                f"{self._base}/repos/{self._slug}/releases/latest"
            )
            if r.status_code == 200:
                d = r.json()
                tag, url = d.get("tag_name"), d.get("html_url")
                return (tag, url) if tag and url else None
            if r.status_code == 404:
                # No Release published. `git push --tags` alone already ships an
                # image, so a repo can legitimately have tags and no releases.
                return await self._latest_tag()
        except Exception:  # noqa: BLE001 — see docstring
            log.debug("Update check failed", exc_info=True)
        return None

    async def _latest_tag(self) -> tuple[str, str] | None:
        try:
            r = await self._http.get(f"{self._base}/repos/{self._slug}/tags")
            if r.status_code != 200:
                return None
            names = [t.get("name") for t in r.json() if isinstance(t, dict)]
            versioned = sorted((n for n in names if _parse(n)), key=_parse, reverse=True)
            if not versioned:
                return None
            top = versioned[0]
            return (top, f"https://github.com/{self._slug}/releases/tag/{top}")
        except Exception:  # noqa: BLE001
            log.debug("Tag lookup failed", exc_info=True)
            return None


def channel_link() -> str:
    return f'<a href="{UPDATES_CHANNEL}">{UPDATES_CHANNEL_HANDLE}</a>'


def update_notice(current: str, latest: str, url: str) -> str:
    """The one-time 'a new version exists' message.

    It deliberately carries no commands. How you upgrade depends on how you
    installed, and the bot guessing wrong would send someone down a path that
    doesn't apply to them — the release notes say it properly, per release."""
    return (
        f"🚀 <b>Update available</b> — v{_display(latest)}\n"
        f"You're running v{_display(current)}.\n\n"
        f'📖 <a href="{escape_html(url)}">Release notes &amp; how to upgrade</a>\n'
        f"📣 {channel_link()} for release news"
    )


_CHECK_EVERY = 60 * 60


async def update_check_loop(
    store,
    client_provider,
    notifier,
    stop: asyncio.Event,
    wake: asyncio.Event | None = None,
    interval_seconds: float = _CHECK_EVERY,
    current: str = __version__,
) -> None:
    """Watch for a newer release and announce it once.

    /status carries the version for anyone who looks, but most people never
    look — so a genuinely new version is worth one message pointing at the
    release notes. Repeats are suppressed by remembering the tag we announced,
    so upgrading is the only thing that makes it speak again."""
    from h1monitor.poller import sleep_until_due

    while not stop.is_set():
        client = client_provider()
        try:
            found = await client.latest_release()
        except Exception:  # noqa: BLE001 — belt and braces; the client swallows already
            log.debug("Update check raised", exc_info=True)
            found = None
        finally:
            await client.aclose()

        if found:
            tag, url = found
            known = store.get_known_release()
            already = known[0] if known else None
            if is_newer(tag, current) and tag != already:
                if await notifier.send_text(update_notice(current, tag, url)):
                    store.set_known_release(tag, url)
            elif tag != already:
                # Remember it either way, so /status can show it without a fetch.
                store.set_known_release(tag, url)

        if stop.is_set():
            break
        await sleep_until_due(stop, wake, interval_seconds)
