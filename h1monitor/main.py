from __future__ import annotations

import asyncio
import logging
import signal

import sys

from h1monitor.config import load_settings, Settings, ConfigError
from h1monitor.store import Store
from h1monitor.bot import build_application
from h1monitor.notifier import Notifier
from h1monitor.h1_client import H1Client
from h1monitor.directory_client import DirectoryClient
from h1monitor.poller import api_poll_loop
from h1monitor.directory_poller import directory_poll_loop

log = logging.getLogger("h1monitor")


def seed_credentials_if_present(store: Store, settings: Settings) -> None:
    if (
        store.get_h1_credentials() is None
        and settings.seed_h1_username
        and settings.seed_h1_token
    ):
        store.set_h1_credentials(settings.seed_h1_username, settings.seed_h1_token)


class LazyNotifier(Notifier):
    """Notifier that resolves the owner chat id lazily on each send, so alerts
    work as soon as the owner is captured on first /start."""

    def __init__(self, bot, resolve_chat_id):
        self._bot = bot
        self._resolve = resolve_chat_id

    async def send_text(self, text: str) -> None:
        chat = self._resolve()
        if chat is None:
            log.info("Suppressed alert (no owner chat yet): %s", text[:60])
            return
        await self._bot.send_message(
            chat, text, parse_mode="HTML", disable_web_page_preview=True
        )


async def main_async(base_dir: str = ".") -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = load_settings(base_dir=base_dir)
    store = Store(settings.db_path, settings.secret_key)
    seed_credentials_if_present(store, settings)

    app = build_application(settings, store)
    stop = asyncio.Event()

    def resolve_chat_id() -> int | None:
        return settings.owner_chat_id or store.get_owner_chat_id()

    notifier = LazyNotifier(app.bot, resolve_chat_id)

    def h1_provider(username: str, token: str) -> H1Client:
        return H1Client(username, token)

    def dir_provider() -> DirectoryClient:
        return DirectoryClient(cookie=settings.directory_cookie)

    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    log.info("Bot started; polling for commands and changes.")

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    try:
        await asyncio.gather(
            api_poll_loop(store, h1_provider, notifier, stop),
            directory_poll_loop(store, dir_provider, notifier, stop),
        )
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        store.close()
        log.info("Shut down cleanly.")


def run() -> None:
    try:
        asyncio.run(main_async())
    except ConfigError as e:
        print(f"Configuration error: {e}\nSet it in your environment or .env file.",
              file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        pass
