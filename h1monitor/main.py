from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from collections.abc import Callable

from telegram.error import NetworkError

from h1monitor.config import (
    load_settings, Settings, ConfigError, load_dotenv, upsert_env_var,
)
from h1monitor.store import Store
from h1monitor.bot import build_application, BOT_COMMANDS, unclaimed, claim_code
from h1monitor.notifier import Notifier, split_for_telegram, describe_error
from h1monitor.h1_client import H1Client
from h1monitor.directory_client import DirectoryClient
from h1monitor.poller import private_poll_loop
from h1monitor.directory_poller import public_poll_loop
from h1monitor.updates import UpdateClient, update_check_loop

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


def claim_banner(code: str) -> str:
    """The startup notice telling the operator how to take ownership. Loud on
    purpose: until someone claims the bot it answers nobody, and a quiet line
    scrolls past in a log people only read when something is already wrong."""
    rule = "=" * 60
    return (
        f"\n{rule}\n"
        "  This bot has no owner yet, so it will not answer anyone.\n"
        "  Open Telegram, find your bot, and send it exactly:\n"
        f"\n      /start {code}\n\n"
        "  That claims it. Nobody else can, without this code.\n"
        f"{rule}"
    )


def print_claim_code(base_dir: str = ".") -> int:
    """`python -m h1monitor --claim-code` — show the code without reading logs."""
    try:
        settings = load_settings(base_dir=base_dir)
    except ConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 1
    store = Store(settings.db_path, settings.secret_key)
    try:
        if not unclaimed(store, settings):
            print("This bot is already claimed.")
            return 0
        print(claim_code(store))
        return 0
    finally:
        store.close()


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


class NetworkNoiseFilter(logging.Filter):
    """Collapse python-telegram-bot's retry tracebacks into a single line.

    When the network drops, the updater logs a full multi-page traceback for
    every retry — a two-minute DNS outage buries the log in a wall of identical
    stack traces that reads like a crash. It isn't: the updater backs off and
    recovers on its own. Keep the line (and the cause), drop the stack, and say
    plainly that it's retrying. Anything that isn't a transient network error
    passes through untouched, traceback and all."""

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        if isinstance(exc, NetworkError):
            record.exc_info = None
            record.exc_text = None
            record.msg = "Telegram unreachable (%s) — retrying."
            record.args = (describe_error(exc),)
            record.levelno = logging.WARNING
            record.levelname = "WARNING"
        return True


async def main_async(base_dir: str = ".") -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # httpx logs full request URLs at INFO — those include the bot token in the
    # api.telegram.org/bot<TOKEN>/... path. Silence them so secrets never hit logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext.Updater").addFilter(NetworkNoiseFilter())
    ensure_bot_token(base_dir)
    settings = load_settings(base_dir=base_dir)
    store = Store(settings.db_path, settings.secret_key)
    seed_credentials_if_present(store, settings)

    # One waker per loop, so an in-chat change (/setup saving a key, /config
    # shortening an interval) interrupts that loop's sleep instead of waiting
    # it out. Handlers run on this same event loop, so set() is enough.
    wakers = {
        "public": asyncio.Event(),
        "private": asyncio.Event(),
        "updates": asyncio.Event(),
    }

    def wake(which: str) -> None:
        waker = wakers.get(which)
        if waker is not None:
            waker.set()

    # Before we touch the network: an unreachable Telegram must not be able to
    # hide the one thing the operator needs to read.
    if unclaimed(store, settings):
        log.warning("%s", claim_banner(claim_code(store)))

    app = build_application(settings, store, wake=wake)
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
    # drop_pending_updates matters for the claim: Telegram holds messages sent
    # while the bot was down for ~24h, so without this a /start typed by someone
    # who found the bot before it ever ran would be delivered first and win.
    await app.updater.start_polling(drop_pending_updates=True)
    log.info("Bot started; polling for commands and changes.")

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    try:
        await asyncio.gather(
            public_poll_loop(store, dir_provider, notifier, stop, wakers["public"]),
            private_poll_loop(store, h1_provider, notifier, stop, wakers["private"]),
            update_check_loop(
                store, UpdateClient, notifier, stop, wakers["updates"]
            ),
        )
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        store.close()
        log.info("Shut down cleanly.")


def run() -> None:
    if "--claim-code" in sys.argv[1:]:
        raise SystemExit(print_claim_code())
    try:
        asyncio.run(main_async())
    except ConfigError as e:
        print(f"Configuration error: {e}\nSet it in your environment or .env file.",
              file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        pass
