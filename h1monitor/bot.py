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
    BotCommand("programs", "How many programs are monitored"),
    BotCommand("status", "Poll interval, credentials, settings"),
    BotCommand("help", "List all commands"),
]


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


# Human-friendly labels for each alert type (used in /config buttons).
CHANGE_LABELS: dict[ChangeType, str] = {
    ChangeType.NEW_PUBLIC_PROGRAM: "🆕 New program launched",
    ChangeType.SCOPE_ADDED: "➕ Scope added",
    ChangeType.SCOPE_REMOVED: "➖ Scope removed",
    ChangeType.SCOPE_MODIFIED: "✏️ Scope changed",
    ChangeType.BOUNTY_CHANGED: "💰 Bounty changed",
    ChangeType.PROGRAM_ADDED: "🔓 New access granted",
    ChangeType.PROGRAM_REMOVED: "🚫 Access removed",
    ChangeType.PROGRAM_STATE: "⏸ Program status",
}


def change_type_label(t: ChangeType) -> str:
    return CHANGE_LABELS.get(t, t.value)


# --- check-interval controls ---
# Preset ladders the +/- steppers walk through (minutes).
PUBLIC_INTERVALS = [15, 30, 60, 120, 240, 360, 720, 1440]
# Private starts at 30 min: a full private sweep takes ~10-13 min, so we keep a
# comfortable margin before the next scan begins.
PRIVATE_INTERVALS = [30, 60, 120, 240, 360, 720, 1440]

# Which Preferences field each side maps to.
_INTERVAL_FIELD = {
    "public": "poll_interval_minutes",
    "private": "private_interval_minutes",
}
_INTERVAL_PRESETS = {"public": PUBLIC_INTERVALS, "private": PRIVATE_INTERVALS}


def format_interval(mins: int) -> str:
    """Render a minute count as a friendly duration: 30 min / 2 h / 1 day."""
    if mins < 60:
        return f"{mins} min"
    if mins % 1440 == 0:
        d = mins // 1440
        return f"{d} day" + ("s" if d != 1 else "")
    if mins % 60 == 0:
        return f"{mins // 60} h"
    h, m = divmod(mins, 60)
    return f"{h} h {m} min"


def step_interval(current: int, presets: list[int], direction: int) -> int:
    """Next preset above/below `current`; clamps at the ends of the ladder."""
    if direction > 0:
        higher = [p for p in presets if p > current]
        return min(higher) if higher else current
    lower = [p for p in presets if p < current]
    return max(lower) if lower else current


def apply_interval_step(store: Store, data: str) -> Preferences:
    """Handle an `intv:{public|private}:{+|-}` tap: step and persist."""
    _, which, sign = data.split(":")
    direction = 1 if sign == "+" else -1
    prefs = store.get_preferences()
    field = _INTERVAL_FIELD[which]
    new = step_interval(getattr(prefs, field), _INTERVAL_PRESETS[which], direction)
    setattr(prefs, field, new)
    store.save_preferences(prefs)
    return prefs


def estimate_sweep_minutes(nprograms: int, nscopes: int) -> int:
    """Rough wall-clock for one full private sweep. Each program costs a list
    request plus a scope page per ~100 scopes, at ~1.4s per request (the H1
    scope endpoint is slow and we scan sequentially to stay rate-safe)."""
    requests = nprograms + (nscopes + 99) // 100
    return max(1, round(requests * 1.4 / 60))


def recommend_private_interval(nprograms: int, nscopes: int) -> int:
    """Smallest preset that leaves comfortable headroom over a full sweep, so
    the account is never scanned faster than it can finish."""
    target = max(min(PRIVATE_INTERVALS), estimate_sweep_minutes(nprograms, nscopes) * 3)
    for p in PRIVATE_INTERVALS:
        if p >= target:
            return p
    return PRIVATE_INTERVALS[-1]


def private_recommendation_line(nprograms: int, nscopes: int) -> str:
    if nprograms <= 0:
        return ""
    rec = recommend_private_interval(nprograms, nscopes)
    sweep = estimate_sweep_minutes(nprograms, nscopes)
    return (
        f"\n\n💡 You have <b>{nprograms:,}</b> private programs — one full scan "
        f"takes about <b>{format_interval(sweep)}</b>, so I suggest a private "
        f"check every <b>{format_interval(rec)}</b> or more."
    )


# --- message text (HTML, pure functions so they're testable) ---

def start_text(has_creds: bool) -> str:
    api = "✅ connected" if has_creds else "❌ not set — send /setup"
    return (
        "👋 <b>h1monitor is running</b>\n\n"
        f"🔑 HackerOne API: {api}\n"
        "🔔 Alerts: <b>on</b> — fine-tune them with /config\n\n"
        "Tap the menu (<code>/</code>) to see everything I can do."
    )


def setup_text() -> str:
    return (
        "🔑 <b>Connect your HackerOne API key</b>\n\n"
        "1️⃣ Open your API token page: "
        "<a href=\"https://hackerone.com/settings/api_token/edit\">"
        "hackerone.com/settings/api_token</a>\n"
        "2️⃣ Send me both values from there:\n"
        "<code>/setapikey &lt;username&gt; &lt;token&gt;</code>\n\n"
        "🔒 Your message is <b>deleted the instant</b> I read it, and the key is stored encrypted."
    )


def setapikey_usage() -> str:
    return "⚠️ <b>Usage:</b> <code>/setapikey &lt;username&gt; &lt;token&gt;</code>"


def setapikey_saved() -> str:
    return (
        "🔐 <b>API key saved</b> — and your message was deleted.\n"
        "Your private programs start syncing on the next check."
    )


def config_prompt(npriv: int = 0, priv_sc: int = 0) -> str:
    return (
        "⚙️ <b>Choose what you get alerted about</b>\n"
        "Tap <b>➖ / ➕</b> to change how often I check, or tap any alert to "
        "switch it on or off."
        + private_recommendation_line(npriv, priv_sc)
    )


def programs_text(npub: int, npriv: int, pub_sc: int, priv_sc: int) -> str:
    return (
        "📡 <b>Under watch</b>\n\n"
        f"🌐 Public — <b>{npub:,}</b> programs · <b>{pub_sc:,}</b> scopes\n"
        f"🔒 Private — <b>{npriv:,}</b> programs · <b>{priv_sc:,}</b> scopes\n\n"
        "Every scope, public and private, is tracked automatically."
    )


def status_text(prefs: Preferences, has_creds: bool) -> str:
    api = "✅ connected" if has_creds else "❌ not set"
    paused = "on" if prefs.exclude_paused else "off"
    return (
        "📊 <b>Status</b>\n\n"
        f"🌐 Public check — every <b>{format_interval(prefs.poll_interval_minutes)}</b>\n"
        f"🔒 Private check — every <b>{format_interval(prefs.private_interval_minutes)}</b>\n"
        f"🔑 HackerOne API — {api}\n"
        f"⏸ Skip paused programs — <b>{paused}</b>"
    )


def help_text() -> str:
    return (
        "🛠 <b>h1monitor commands</b>\n\n"
        "/start — status &amp; setup\n"
        "/setapikey — connect your API key (auto-deleted)\n"
        "/config — choose which alerts you receive\n"
        "/programs — how many programs &amp; scopes are watched\n"
        "/status — check intervals &amp; settings\n"
        "/help — show this list\n\n"
        "Everything — public <i>and</i> private, programs <i>and</i> scopes — "
        "is monitored automatically."
    )


def _interval_row(label: str, which: str, mins: int) -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton("➖", callback_data=f"intv:{which}:-"),
        InlineKeyboardButton(
            f"{label} {format_interval(mins)}", callback_data="noop"
        ),
        InlineKeyboardButton("➕", callback_data=f"intv:{which}:+"),
    ]


def build_config_keyboard(prefs: Preferences) -> InlineKeyboardMarkup:
    rows = [
        _interval_row("🌐 Public:", "public", prefs.poll_interval_minutes),
        _interval_row("🔒 Private:", "private", prefs.private_interval_minutes),
    ]
    for t in ChangeType:
        mark = "✅" if prefs.is_type_enabled(t) else "❌"
        rows.append(
            [InlineKeyboardButton(
                f"{mark} {change_type_label(t)}", callback_data=f"toggle:{t.value}"
            )]
        )
    mark = "✅" if prefs.exclude_paused else "❌"
    rows.append(
        [InlineKeyboardButton(
            f"{mark} ⏸ Ignore paused programs", callback_data="toggle:exclude_paused"
        )]
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
        await update.message.reply_text(start_text(has), parse_mode="HTML")

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
                setapikey_usage(), parse_mode="HTML"
            )
            return
        store.set_h1_credentials(*parsed)
        await update.effective_chat.send_message(setapikey_saved(), parse_mode="HTML")

    async def setup(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not guard(update):
            return
        await update.message.reply_text(setup_text(), parse_mode="HTML")

    async def config(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not guard(update):
            return
        priv = store.load_snapshot("private")
        npriv = len(priv.programs) if priv else 0
        priv_sc = sum(len(p.scopes) for p in priv.programs.values()) if priv else 0
        await update.message.reply_text(
            config_prompt(npriv, priv_sc),
            parse_mode="HTML",
            reply_markup=build_config_keyboard(store.get_preferences()),
        )

    async def on_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not guard(update):
            return
        q = update.callback_query
        prefs = apply_toggle(store, q.data)
        await q.answer("Updated")
        await q.edit_message_reply_markup(build_config_keyboard(prefs))

    async def on_interval(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not guard(update):
            return
        q = update.callback_query
        prefs = apply_interval_step(store, q.data)
        which = q.data.split(":")[1]
        mins = getattr(prefs, _INTERVAL_FIELD[which])
        await q.answer(f"Every {format_interval(mins)}")
        await q.edit_message_reply_markup(build_config_keyboard(prefs))

    async def on_noop(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        # The interval label button carries no action.
        await update.callback_query.answer()

    async def programs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not guard(update):
            return
        pub = store.load_snapshot("public")
        priv = store.load_snapshot("private")
        npub = len(pub.programs) if pub else 0
        npriv = len(priv.programs) if priv else 0
        pub_sc = sum(len(p.scopes) for p in pub.programs.values()) if pub else 0
        priv_sc = sum(len(p.scopes) for p in priv.programs.values()) if priv else 0
        await update.message.reply_text(
            programs_text(npub, npriv, pub_sc, priv_sc), parse_mode="HTML"
        )

    async def status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not guard(update):
            return
        prefs = store.get_preferences()
        has = store.get_h1_credentials() is not None
        await update.message.reply_text(status_text(prefs, has), parse_mode="HTML")

    async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not guard(update):
            return
        await update.message.reply_text(help_text(), parse_mode="HTML")

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setup", setup))
    app.add_handler(CommandHandler("setapikey", setapikey))
    app.add_handler(CommandHandler("config", config))
    app.add_handler(CommandHandler("programs", programs))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CallbackQueryHandler(on_toggle, pattern=r"^toggle:"))
    app.add_handler(CallbackQueryHandler(on_interval, pattern=r"^intv:"))
    app.add_handler(CallbackQueryHandler(on_noop, pattern=r"^noop$"))
    return app
