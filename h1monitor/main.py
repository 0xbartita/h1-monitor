from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from collections.abc import Callable

from h1monitor.config import (
    load_settings, Settings, ConfigError, load_dotenv, upsert_env_var,
)
from h1monitor.store import Store
from h1monitor.bot import build_application, BOT_COMMANDS
from h1monitor.notifier import Notifier, split_for_telegram
from h1monitor.h1_client import H1Client
from h1monitor.directory_client import DirectoryClient
from h1monitor.poller import private_poll_loop
from h1monitor.directory_poller import public_poll_loop

log = logging.getLogger("h1monitor")


def ensure_bot_token(
    base_dir: str = ".",
    *,
    input_fn: Callable[[str], str] = input,
    isatty: bool | None = None,
    out: Callable[..., None] = print,
) -> None:
    """First-run helper: if no Telegram bot token is configured and we're on an
    interactive terminal, prompt for it and save it to .env. Non-interactive
    runs (systemd/Docker) fall through to a clean ConfigError in load_settings."""
    merged = {**load_dotenv(base_dir), **os.environ}
    if merged.get("TELEGRAM_BOT_TOKEN"):
        return
    tty = sys.stdin.isatty() if isatty is None else isatty
    if not tty:
        return
    out("\nFirst-time setup — h1monitor needs your Telegram bot token.")
    out("Create one by messaging @BotFather on Telegram (send /newbot), then paste it here.")
    token = ""
    while not token:
        token = input_fn("Telegram bot token: ").strip()
    upsert_env_var(base_dir, "TELEGRAM_BOT_TOKEN", token)
    out("Saved to .env — starting now. You won't be asked again.\n")


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

    async def send_text(self, text: str) -> bool:
        """Deliver to the owner chat, splitting oversized messages. Never raises:
        a Telegram outage must not escape a poll loop and kill the daemon."""
        chat = self._resolve()
        if chat is None:
            log.info("Suppressed alert (no owner chat yet): %s", text[:60])
            return False
        try:
            for chunk in split_for_telegram(text):
                await self._bot.send_message(
                    chat, chunk, parse_mode="HTML", disable_web_page_preview=True
                )
            return True
        except Exception:  # noqa: BLE001 — send failures are logged, never fatal
            log.warning("Failed to deliver Telegram message", exc_info=True)
            return False


async def main_async(base_dir: str = ".") -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # httpx logs full request URLs at INFO — those include the bot token in the
    # api.telegram.org/bot<TOKEN>/... path. Silence them so secrets never hit logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    ensure_bot_token(base_dir)
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
    # Register the command list so Telegram shows an autocomplete menu on "/".
    # (post_init only fires via run_polling(), which we don't use.)
    await app.bot.set_my_commands(BOT_COMMANDS)
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
            public_poll_loop(store, dir_provider, notifier, stop),
            private_poll_loop(store, h1_provider, notifier, stop),
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
