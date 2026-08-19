from __future__ import annotations

import asyncio
import time

from h1monitor.store import Store
from h1monitor.notifier import Notifier
from h1monitor.differ import diff_api
from h1monitor.filters import filter_changes


async def run_api_cycle(store: Store, client, notifier: Notifier) -> None:
    previous = store.load_api_snapshot()
    snap = await client.fetch_snapshot(previous)
    if previous is None:
        store.save_api_snapshot(snap)
        await notifier.send_text(
            f"✅ Baseline established — watching {len(snap.programs)} accessible programs."
        )
        store.record_poll("api", time.time())
        return
    changes = filter_changes(diff_api(previous, snap), store.get_preferences())
    await notifier.send_changes(changes)
    store.save_api_snapshot(snap)
    store.record_poll("api", time.time())


async def api_poll_loop(
    store: Store, client_provider, notifier: Notifier, stop: asyncio.Event
) -> None:
    while not stop.is_set():
        creds = store.get_h1_credentials()
        if creds is None:
            if store.mark_alert_sent("api-no-creds"):
                await notifier.send_text("ℹ️ No H1 credentials yet — run /setup.")
        else:
            store.clear_alert("api-no-creds")
            client = client_provider(*creds)
            try:
                await run_api_cycle(store, client, notifier)
                store.clear_alert("api-fetch-failed")
            except Exception as e:  # noqa: BLE001
                if store.mark_alert_sent("api-fetch-failed"):
                    await notifier.send_text(f"⚠️ H1 API poll failed: {e}")
            finally:
                await client.aclose()
        interval = store.get_preferences().poll_interval_minutes * 60
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
