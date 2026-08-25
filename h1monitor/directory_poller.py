from __future__ import annotations

import asyncio
import time

from h1monitor.store import Store
from h1monitor.notifier import Notifier, escape_html, describe_error
from h1monitor.poller import await_or_stop, send_deduped_alert, sleep_until_due
from h1monitor.differ import diff_snapshot
from h1monitor.filters import filter_changes
from h1monitor.models import ChangeType


async def run_public_cycle(store: Store, client, notifier: Notifier) -> None:
    """Diff all public programs (via the directory GraphQL — no API key)."""
    prefs = store.get_preferences()
    previous = store.load_snapshot("public")
    snap = await client.fetch_public_snapshot(previous)
    if previous is None:
        store.save_snapshot("public", snap)
        await notifier.send_text(
            f"✅ <b>Now tracking {len(snap.programs):,} public programs</b> — "
            "you'll hear about new launches and scope changes."
        )
        store.record_poll("public", time.time())
        return
    changes = filter_changes(
        diff_snapshot(previous, snap, ChangeType.NEW_PUBLIC_PROGRAM), prefs
    )
    await notifier.send_changes(changes)
    store.save_snapshot("public", snap)
    store.record_poll("public", time.time())


async def public_poll_loop(
    store: Store,
    client_provider,
    notifier: Notifier,
    stop: asyncio.Event,
    wake: asyncio.Event | None = None,
) -> None:
    while not stop.is_set():
        client = client_provider()
        try:
            if await await_or_stop(run_public_cycle(store, client, notifier), stop):
                store.clear_alert("public-fetch-failed")
        except Exception as e:  # noqa: BLE001
            await send_deduped_alert(
                store, notifier, "public-fetch-failed",
                f"⚠️ <b>Public sync failed:</b> {escape_html(describe_error(e))}",
            )
        finally:
            await client.aclose()
        if stop.is_set():
            break
        interval = store.get_preferences().poll_interval_minutes * 60
        await sleep_until_due(stop, wake, interval)
