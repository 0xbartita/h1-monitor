from __future__ import annotations

import asyncio
import time

from h1monitor.store import Store
from h1monitor.notifier import Notifier, escape_html, describe_error
from h1monitor.differ import diff_snapshot
from h1monitor.filters import filter_changes
from h1monitor.models import ChangeType


async def run_private_cycle(store: Store, client, notifier: Notifier) -> None:
    """Diff the operator's private programs (via their API key)."""
    prefs = store.get_preferences()
    previous = store.load_snapshot("private")
    snap = await client.fetch_private_snapshot(previous)
    if previous is None:
        store.save_snapshot("private", snap)
        await notifier.send_text(
            f"✅ <b>Baseline ready</b> — now watching <b>{len(snap.programs):,}</b> "
            "private program(s). Change alerts start from the next check."
        )
        store.record_poll("private", time.time())
        return
    changes = filter_changes(
        diff_snapshot(previous, snap, ChangeType.PROGRAM_ADDED), prefs
    )
    await notifier.send_changes(changes)
    store.save_snapshot("private", snap)
    store.record_poll("private", time.time())


async def private_poll_loop(
    store: Store, client_provider, notifier: Notifier, stop: asyncio.Event
) -> None:
    while not stop.is_set():
        creds = store.get_h1_credentials()
        if creds is None:
            if store.mark_alert_sent("private-no-creds"):
                await notifier.send_text(
                    "ℹ️ <b>No HackerOne key yet</b> — send /setup to start watching "
                    "your private programs."
                )
        else:
            store.clear_alert("private-no-creds")
            client = client_provider(*creds)
            try:
                await run_private_cycle(store, client, notifier)
                store.clear_alert("private-fetch-failed")
            except Exception as e:  # noqa: BLE001
                if store.mark_alert_sent("private-fetch-failed"):
                    await notifier.send_text(
                        f"⚠️ <b>Private sync failed:</b> {escape_html(describe_error(e))}"
                    )
            finally:
                await client.aclose()
        interval = store.get_preferences().private_interval_minutes * 60
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
