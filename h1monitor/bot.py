from __future__ import annotations

import logging
import secrets

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
)

from h1monitor.store import Store
from h1monitor.config import Settings
from h1monitor.models import Preferences, ChangeType
from h1monitor import __version__
from h1monitor.notifier import escape_html
from h1monitor.updates import is_newer, version_line, channel_link

log = logging.getLogger("h1monitor")

# Registered with Telegram so typing "/" pops up an autocomplete menu.
BOT_COMMANDS = [
    BotCommand("start", "Show status and capture your chat"),
    BotCommand("setup", "Add your HackerOne API key (message auto-deleted)"),
    BotCommand("config", "Choose which alerts you receive"),
    BotCommand("status", "What's watched, check intervals, settings"),
    BotCommand("help", "List all commands"),
]


def is_owner(chat_id: int | None, store: Store, settings: Settings) -> bool:
    """Does this chat own the bot? A pure check — it never claims.

    It used to: the first chat to send any command was written in as owner. Bot
    usernames are searchable and the installer starts the bot the moment it has
    a token, so a stranger who got there first took the bot, received every
    private-program alert, and left the real operator with a bot that answered
    nobody. Claiming is now a deliberate act (see try_claim)."""
    if chat_id is None:
        return False
    if settings.owner_chat_id is not None:
        return chat_id == settings.owner_chat_id
    return chat_id == store.get_owner_chat_id()


def unclaimed(store: Store, settings: Settings) -> bool:
    """True while nobody owns the bot yet and no owner is pinned in .env."""
    return settings.owner_chat_id is None and store.get_owner_chat_id() is None


def claim_code(store: Store) -> str:
    """The one-time code that claims this bot, created on first use.

    Stored rather than generated per run, so a restart doesn't invalidate the
    code the installer already printed."""
    code = store.get_claim_code()
    if not code:
        code = secrets.token_hex(4)
        store.set_claim_code(code)
    return code


def try_claim(
    chat_id: int, text: str | None, store: Store, settings: Settings,
    private: bool = True,
) -> bool:
    """Claim the bot for `chat_id` if `text` is "/start <the right code>".

    Private chats only — a group claim would hand alerts to everyone in it. The
    code is compared in constant time out of habit; the real protection is that
    it never leaves the server the bot runs on."""
    if not unclaimed(store, settings) or not private:
        return False
    parts = (text or "").split()
    supplied = parts[1].strip().lower() if len(parts) > 1 else ""
    if not supplied or not secrets.compare_digest(supplied, claim_code(store)):
        return False
    store.set_owner_chat_id(chat_id)
    store.clear_claim_code()
    return True


def claim_prompt() -> str:
    """Shown to anyone who talks to an unclaimed bot. Says how to find the code,
    never the code itself — that only exists on the machine running the bot."""
    return (
        "\U0001F512 <b>This bot has no owner yet</b>\n\n"
        "To claim it, send:\n"
        "<code>/start YOUR-CODE</code>\n\n"
        "Your code was printed when the bot started. To see it again:\n"
        "\u2022 script install \u2014 <code>journalctl --user -u h1monitor | grep -i claim</code>\n"
        "\u2022 Docker \u2014 <code>docker logs h1monitor | grep -i claim</code>"
    )


def already_claimed_text() -> str:
    return (
        "\U0001F512 <b>This bot is already in use.</b>\n"
        "It only answers the person who set it up."
    )


def deny_text(store: Store, settings: Settings) -> str:
    return claim_prompt() if unclaimed(store, settings) else already_claimed_text()


def parse_setup_args(text: str) -> tuple[str, str] | None:
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


def apply_interval_step(store: Store, data: str, wake=None) -> Preferences:
    """Handle an `intv:{public|private}:{+|-}` tap: step, persist, and wake that
    loop — a shortened interval should take effect now, not after the old (and
    longer) sleep it is already sitting in has run out."""
    _, which, sign = data.split(":")
    direction = 1 if sign == "+" else -1
    prefs = store.get_preferences()
    field = _INTERVAL_FIELD[which]
    new = step_interval(getattr(prefs, field), _INTERVAL_PRESETS[which], direction)
    setattr(prefs, field, new)
    store.save_preferences(prefs)
    if wake is not None:
        wake(which)
    return prefs


def save_h1_credentials(store: Store, username: str, token: str, wake=None) -> None:
    """Store the operator's API key and wake the private loop, so their first
    sweep starts within seconds. Without the wake the loop stays asleep in the
    interval it entered at startup — up to 2h of '/status: Private — 0' with
    nothing to suggest anything is wrong."""
    store.set_h1_credentials(username, token)
    if wake is not None:
        wake("private")


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

def start_text(has_creds: bool, version: str = __version__, latest=None) -> str:
    if has_creds:
        api = "✅ connected"
        setup_line = "🔗 Use /setup anytime to update your HackerOne key."
    else:
        api = "❌ not connected"
        setup_line = "👉 Tap /setup to connect your HackerOne account."
    return (
        "👋 <b>h1monitor is running</b>\n\n"
        f"🔑 HackerOne API: {api}\n"
        f"{setup_line}\n"
        "🔔 Alerts: <b>on</b> — fine-tune them with /config\n\n"
        + _update_line(version, latest)
        + "Tap the menu (<code>/</code>) to see everything I can do.\n\n"
        f"🏷 v{version} · 📣 {channel_link()} for release news"
    )


def _update_line(version: str, latest) -> str:
    """A single nudge when a newer release exists, and nothing at all when it
    doesn't — a version banner on every /start would just be noise."""
    if not latest:
        return ""
    tag, url = latest
    if not is_newer(tag, version):
        return ""
    return (
        f'🚀 <b>v{tag.lstrip("vV")} is available</b> — '
        f'<a href="{escape_html(url)}">release notes</a>. '
        "They explain how to upgrade.\n\n"
    )


def setup_text() -> str:
    return (
        "🔑 <b>Connect your HackerOne API key</b>\n\n"
        "1️⃣ Open your API token page: "
        "<a href=\"https://hackerone.com/settings/api_token/edit\">"
        "hackerone.com/settings/api_token</a>\n"
        "2️⃣ Send me both values from there:\n"
        "<code>/setup &lt;username&gt; &lt;token&gt;</code>\n\n"
        "🔒 Your message is <b>deleted the instant</b> I read it, and the key is stored encrypted."
    )


def setup_usage() -> str:
    return "⚠️ <b>Usage:</b> <code>/setup &lt;username&gt; &lt;token&gt;</code>"


def setup_saved() -> str:
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


def status_text(
    prefs: Preferences,
    has_creds: bool,
    npub: int,
    npriv: int,
    pub_sc: int,
    priv_sc: int,
    version: str = __version__,
    latest=None,
) -> str:
    api = "✅ connected" if has_creds else "❌ not set"
    paused = "on" if prefs.exclude_paused else "off"
    tag, url = latest if latest else (None, None)
    return (
        "📊 <b>Status</b>\n\n"
        f"🌐 Public — <b>{npub:,}</b> programs · <b>{pub_sc:,}</b> scopes\n"
        f"🔒 Private — <b>{npriv:,}</b> programs · <b>{priv_sc:,}</b> scopes\n\n"
        f"🌐 Public check — every <b>{format_interval(prefs.poll_interval_minutes)}</b>\n"
        f"🔒 Private check — every <b>{format_interval(prefs.private_interval_minutes)}</b>\n"
        f"🔑 HackerOne API — {api}\n"
        f"⏸ Skip paused programs — <b>{paused}</b>\n\n"
        + version_line(version, tag, url)
    )


def help_text() -> str:
    return (
        "🛠 <b>h1monitor commands</b>\n\n"
        "/start — status &amp; setup\n"
        "/setup — connect your API key (message auto-deleted)\n"
        "/config — choose which alerts you receive\n"
        "/status — what's watched, check intervals &amp; settings\n"
        "/help — show this list\n\n"
        "Everything — public <i>and</i> private, programs <i>and</i> scopes — "
        "is monitored automatically.\n\n"
        f"📣 Join {channel_link()} to hear about new versions."
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


def build_application(settings: Settings, store: Store, wake=None) -> Application:
    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(_post_init)
        .build()
    )

    async def guard(update: Update) -> bool:
        """True when the sender owns this bot; otherwise say so and refuse.

        Refusing silently used to mean a locked-out operator saw nothing at all
        and had no way to tell a bug from a hijack."""
        chat = update.effective_chat
        if is_owner(chat.id if chat else None, store, settings):
            return True
        log.warning(
            "Refused a command from chat %s (not the owner)",
            chat.id if chat else None,
        )
        msg = update.effective_message
        if msg is not None:
            try:
                await msg.reply_text(deny_text(store, settings), parse_mode="HTML")
            except Exception:  # noqa: BLE001 — a failed refusal must not raise
                pass
        return False

    async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        chat, msg = update.effective_chat, update.effective_message
        if chat is None or msg is None:
            return
        if not is_owner(chat.id, store, settings):
            if not unclaimed(store, settings):
                log.warning("Refused /start from chat %s (not the owner)", chat.id)
                await msg.reply_text(already_claimed_text(), parse_mode="HTML")
                return
            if not try_claim(chat.id, msg.text, store, settings,
                             private=chat.type == "private"):
                log.warning("Rejected a claim attempt from chat %s", chat.id)
                await msg.reply_text(claim_prompt(), parse_mode="HTML")
                return
            log.info("Bot claimed by chat %s", chat.id)
        has = store.get_h1_credentials() is not None
        await msg.reply_text(
            start_text(has, latest=store.get_known_release()),
            parse_mode="HTML", disable_web_page_preview=True,
        )

    async def setup(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await guard(update):
            return
        text = update.effective_message.text or ""
        # Plain "/setup" → show instructions.
        if len(text.split()) <= 1:
            await update.effective_message.reply_text(setup_text(), parse_mode="HTML")
            return
        # "/setup <username> <token>" → the message carries the key, so delete it
        # first (before anything can fail), then save or explain the format.
        try:
            await update.effective_message.delete()
        except Exception:
            pass
        parsed = parse_setup_args(text)
        if not parsed:
            await update.effective_chat.send_message(setup_usage(), parse_mode="HTML")
            return
        save_h1_credentials(store, *parsed, wake=wake)
        await update.effective_chat.send_message(setup_saved(), parse_mode="HTML")

    async def config(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await guard(update):
            return
        priv = store.load_snapshot("private")
        npriv = len(priv.programs) if priv else 0
        priv_sc = sum(len(p.scopes) for p in priv.programs.values()) if priv else 0
        await update.effective_message.reply_text(
            config_prompt(npriv, priv_sc),
            parse_mode="HTML",
            reply_markup=build_config_keyboard(store.get_preferences()),
        )

    async def on_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await guard(update):
            return
        q = update.callback_query
        prefs = apply_toggle(store, q.data)
        await q.answer("Updated")
        await q.edit_message_reply_markup(build_config_keyboard(prefs))

    async def on_interval(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await guard(update):
            return
        q = update.callback_query
        prefs = apply_interval_step(store, q.data, wake=wake)
        which = q.data.split(":")[1]
        mins = getattr(prefs, _INTERVAL_FIELD[which])
        await q.answer(f"Every {format_interval(mins)}")
        await q.edit_message_reply_markup(build_config_keyboard(prefs))

    async def on_noop(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        # The interval label button carries no action.
        await update.callback_query.answer()

    async def status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await guard(update):
            return
        prefs = store.get_preferences()
        has = store.get_h1_credentials() is not None
        pub = store.load_snapshot("public")
        priv = store.load_snapshot("private")
        npub = len(pub.programs) if pub else 0
        npriv = len(priv.programs) if priv else 0
        pub_sc = sum(len(p.scopes) for p in pub.programs.values()) if pub else 0
        priv_sc = sum(len(p.scopes) for p in priv.programs.values()) if priv else 0
        await update.effective_message.reply_text(
            status_text(
                prefs, has, npub, npriv, pub_sc, priv_sc,
                latest=store.get_known_release(),
            ),
            parse_mode="HTML", disable_web_page_preview=True,
        )

    async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await guard(update):
            return
        await update.effective_message.reply_text(help_text(), parse_mode="HTML")

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setup", setup))
    app.add_handler(CommandHandler("config", config))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CallbackQueryHandler(on_toggle, pattern=r"^toggle:"))
    app.add_handler(CallbackQueryHandler(on_interval, pattern=r"^intv:"))
    app.add_handler(CallbackQueryHandler(on_noop, pattern=r"^noop$"))
    return app
