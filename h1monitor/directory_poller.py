from __future__ import annotations

import asyncio
import time

from h1monitor.store import Store
from h1monitor.notifier import Notifier
from h1monitor.differ import diff_directory
from h1monitor.filters import filter_changes


async def run_directory_cycle(store: Store, client, notifier: Notifier) -> None:
    progs = await client.fetch_all()
    first = not store.has_directory_baseline()
    changes = filter_changes(
        diff_directory(store.load_directory_handles(), progs, first),
        store.get_preferences(),
    )
    if first:
        await notifier.send_text(f"✅ Tracking {len(progs)} directory programs.")
    else:
        await notifier.send_changes(changes)
    store.save_directory_handles({p.handle for p in progs})
    store.record_poll("directory", time.time())


async def directory_poll_loop(
    store: Store, client_provider, notifier: Notifier, stop: asyncio.Event
) -> None:
    while not stop.is_set():
        client = client_provider()
        try:
            await run_directory_cycle(store, client, notifier)
            store.clear_alert("dir-fetch-failed")
        except Exception as e:  # noqa: BLE001
            if store.mark_alert_sent("dir-fetch-failed"):
                await notifier.send_text(f"⚠️ Directory poll failed: {e}")
        finally:
            await client.aclose()
        interval = store.get_preferences().poll_interval_minutes * 60
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
