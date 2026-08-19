from __future__ import annotations

import asyncio
import logging
import time

from h1monitor.store import Store
from h1monitor.notifier import Notifier, escape_html, describe_error
from h1monitor.differ import diff_snapshot
from h1monitor.filters import filter_changes
from h1monitor.models import ChangeType

log = logging.getLogger("h1monitor")


async def await_or_stop(coro, stop: asyncio.Event) -> bool:
    """Run `coro` as a task but abandon it if `stop` is set first, so a SIGTERM
    during a multi-minute sweep shuts down promptly instead of waiting it out.
    Returns True if the task completed (re-raising its exception to the caller),
    False if it was cancelled because shutdown was requested mid-cycle."""
    task = asyncio.ensure_future(coro)
    stopper = asyncio.ensure_future(stop.wait())
    try:
        await asyncio.wait({task, stopper}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        stopper.cancel()
    if task.done():
        task.result()  # surface any exception to the caller's except handler
        return True
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return False


async def send_deduped_alert(store: Store, notifier, key: str, text: str) -> None:
    """Send a one-shot alert (deduped by `key`). If delivery fails, clear the
    dedup row so the alert retries next cycle instead of going silent forever.
    Never raises — a failing notifier must not be able to kill a poll loop."""
    if not store.mark_alert_sent(key):
        return
    try:
        delivered = await notifier.send_text(text)
    except Exception:  # noqa: BLE001 — belt-and-suspenders around the notifier
        log.warning("Alert delivery raised", exc_info=True)
        delivered = False
    if not delivered:
        store.clear_alert(key)


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
            await send_deduped_alert(
                store, notifier, "private-no-creds",
                "ℹ️ <b>No HackerOne key yet</b> — send /setup to start watching "
                "your private programs.",
            )
        else:
            store.clear_alert("private-no-creds")
            client = client_provider(*creds)
            try:
                if await await_or_stop(
                    run_private_cycle(store, client, notifier), stop
                ):
                    store.clear_alert("private-fetch-failed")
            except Exception as e:  # noqa: BLE001
                await send_deduped_alert(
                    store, notifier, "private-fetch-failed",
                    f"⚠️ <b>Private sync failed:</b> {escape_html(describe_error(e))}",
                )
            finally:
                await client.aclose()
            if stop.is_set():
                break
        interval = store.get_preferences().private_interval_minutes * 60
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
