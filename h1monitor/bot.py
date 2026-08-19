from __future__ import annotations

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
)

from h1monitor.store import Store
from h1monitor.config import Settings
from h1monitor.models import Preferences, ChangeType

# Registered with Telegram so typing "/" pops up an autocomplete menu.
BOT_COMMANDS = [
    BotCommand("start", "Show status and capture your chat"),
    BotCommand("setup", "How to add your HackerOne API key"),
    BotCommand("setapikey", "Set HackerOne API key (message auto-deleted)"),
    BotCommand("config", "Choose which alerts you receive"),
    BotCommand("watch", "Deep-scan a private program's scopes: /watch <handle>"),
    BotCommand("unwatch", "Stop scope-scanning a private program: /unwatch <handle>"),
    BotCommand("watchlist", "Show private programs being scope-scanned"),
    BotCommand("programs", "How many programs are monitored"),
    BotCommand("status", "Poll interval, credentials, settings"),
    BotCommand("help", "List all commands"),
]


def parse_handle_arg(text: str) -> str | None:
    parts = (text or "").split()
    return parts[1] if len(parts) == 2 else None


def is_owner(chat_id: int | None, store: Store, settings: Settings) -> bool:
    if chat_id is None:
        return False
    if settings.owner_chat_id is not None:
        return chat_id == settings.owner_chat_id
    stored = store.get_owner_chat_id()
    if stored is None:
        store.set_owner_chat_id(chat_id)
        return True
    return chat_id == stored


def parse_setapikey_args(text: str) -> tuple[str, str] | None:
    parts = (text or "").split()
    if len(parts) == 3:
        return parts[1], parts[2]
    return None


def build_config_keyboard(prefs: Preferences) -> InlineKeyboardMarkup:
    rows = []
    for t in ChangeType:
        mark = "✅" if prefs.is_type_enabled(t) else "❌"
        rows.append(
            [InlineKeyboardButton(f"{mark} {t.value}", callback_data=f"toggle:{t.value}")]
        )
    mark = "✅" if prefs.exclude_paused else "❌"
    rows.append(
        [InlineKeyboardButton(f"{mark} exclude_paused", callback_data="toggle:exclude_paused")]
    )
    return InlineKeyboardMarkup(rows)


def apply_toggle(store: Store, data: str) -> Preferences:
    key = data.split(":", 1)[1]
    prefs = store.get_preferences()
    if key == "exclude_paused":
        prefs.exclude_paused = not prefs.exclude_paused
    else:
        t = ChangeType(key)
        prefs.enabled[t] = not prefs.is_type_enabled(t)
    store.save_preferences(prefs)
    return prefs


async def _post_init(app: Application) -> None:
    # Publish the command list so Telegram shows an autocomplete menu on "/".
    await app.bot.set_my_commands(BOT_COMMANDS)


def build_application(settings: Settings, store: Store) -> Application:
    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(_post_init)
        .build()
    )

    def guard(update: Update) -> bool:
        chat = update.effective_chat
        return is_owner(chat.id if chat else None, store, settings)

    async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not guard(update):
            return
        has = store.get_h1_credentials() is not None
        msg = (
            "👋 h1monitor ready.\n"
            f"H1 credentials: {'set ✅' if has else 'not set ❌ — run /setup'}\n"
            "Use /config to choose what you receive."
        )
        await update.message.reply_text(msg)

    async def setapikey(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not guard(update):
            return
        parsed = parse_setapikey_args(update.message.text)
        try:
            await update.message.delete()
        except Exception:
            pass
        if not parsed:
            await update.effective_chat.send_message(
                "Usage: /setapikey <identifier> <token>"
            )
            return
        store.set_h1_credentials(*parsed)
        await update.effective_chat.send_message(
            "🔐 H1 credentials saved (your message was deleted)."
        )

    async def setup(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not guard(update):
            return
        await update.message.reply_text(
            "Send: /setapikey <identifier> <token>\n"
            "Your message is deleted immediately after capture."
        )

    async def config(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not guard(update):
            return
        await update.message.reply_text(
            "Toggle what you receive:",
            reply_markup=build_config_keyboard(store.get_preferences()),
        )

    async def on_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not guard(update):
            return
        q = update.callback_query
        prefs = apply_toggle(store, q.data)
        await q.answer("Updated")
        await q.edit_message_reply_markup(build_config_keyboard(prefs))

    async def watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not guard(update):
            return
        handle = parse_handle_arg(update.message.text)
        if not handle:
            await update.message.reply_text("Usage: /watch <program-handle>")
            return
        prefs = store.get_preferences()
        prefs.private_watch = prefs.private_watch | {handle}
        store.save_preferences(prefs)
        await update.message.reply_text(
            f"👁 Now scope-scanning private program '{handle}'. "
            f"({len(prefs.private_watch)} watched; applies on the next private poll.)"
        )

    async def unwatch(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not guard(update):
            return
        handle = parse_handle_arg(update.message.text)
        if not handle:
            await update.message.reply_text("Usage: /unwatch <program-handle>")
            return
        prefs = store.get_preferences()
        prefs.private_watch = prefs.private_watch - {handle}
        store.save_preferences(prefs)
        await update.message.reply_text(f"🚫 Stopped scope-scanning '{handle}'.")

    async def watchlist(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not guard(update):
            return
        watched = sorted(store.get_preferences().private_watch)
        if watched:
            await update.message.reply_text(
                "Scope-scanning these private programs:\n" + "\n".join(f"• {h}" for h in watched)
            )
        else:
            await update.message.reply_text(
                "No private programs are being scope-scanned. Add one with /watch <handle>."
            )

    async def programs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not guard(update):
            return
        pub = store.load_snapshot("public")
        priv = store.load_snapshot("private")
        npub = len(pub.programs) if pub else 0
        npriv = len(priv.programs) if priv else 0
        nwatch = len(store.get_preferences().private_watch)
        await update.message.reply_text(
            f"Monitoring {npub} public + {npriv} private program(s).\n"
            f"Scope-scanning {nwatch} private program(s) (see /watchlist)."
        )

    async def status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not guard(update):
            return
        prefs = store.get_preferences()
        has = store.get_h1_credentials() is not None
        await update.message.reply_text(
            f"Interval: {prefs.poll_interval_minutes}m | "
            f"H1 creds: {'yes' if has else 'no'} | "
            f"exclude_paused: {prefs.exclude_paused}"
        )

    async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not guard(update):
            return
        await update.message.reply_text(
            "/start — status & setup\n"
            "/setapikey <id> <token> — set API key (auto-deleted)\n"
            "/config — toggle which alerts you get\n"
            "/watch <handle> — scope-scan a private program\n"
            "/unwatch <handle> — stop scope-scanning one\n"
            "/watchlist — list scope-scanned private programs\n"
            "/programs — counts\n"
            "/status — settings\n"
            "/help — this list"
        )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setup", setup))
    app.add_handler(CommandHandler("setapikey", setapikey))
    app.add_handler(CommandHandler("config", config))
    app.add_handler(CommandHandler("watch", watch))
    app.add_handler(CommandHandler("unwatch", unwatch))
    app.add_handler(CommandHandler("watchlist", watchlist))
    app.add_handler(CommandHandler("programs", programs))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CallbackQueryHandler(on_toggle, pattern=r"^toggle:"))
    return app
