from __future__ import annotations

from html import escape

from h1monitor.models import Change, ChangeType

_MAX = 3800


def escape_html(s: str) -> str:
    return escape(s or "")


def _program_url(c: Change) -> str:
    if c.directory and c.directory.url:
        return c.directory.url
    return f"https://hackerone.com/{c.program_handle}"


def _pretty_url(url: str) -> str:
    return url.split("://", 1)[-1].rstrip("/")


def _link(url: str) -> str:
    return f'🔗 <a href="{escape_html(url)}">{escape_html(_pretty_url(url))}</a>'


def _format_new_program(c: Change) -> str:
    dp = c.directory
    kind = (
        "Bug bounty program"
        if (dp and dp.offers_bounties)
        else "vulnerability disclosure program"
    )
    name = escape_html(dp.name if dp else c.program_name)
    if dp and dp.started_accepting_at:
        # started_accepting_at arrives as a full ISO timestamp; show date only.
        date = dp.started_accepting_at[:10]
        body = f"launched on {escape_html(date)} as a {kind}."
    else:
        body = f"was newly observed as a {kind}."
    url = dp.url if dp and dp.url else f"https://hackerone.com/{c.program_handle}"
    return (
        f"🆕 <b>New Program: {name}</b>\n"
        f"{name} {body}\n"
        f"{_link(url)}"
    )


_TRANSITION = "<code>{a}</code> → <code>{b}</code>"


def _change_line(c: Change) -> str:
    """One styled line describing a single change, technical bits in monospace."""
    t = c.primary_type
    d = c.details or {}
    key = d.get("scope_key")

    if key and t == ChangeType.SCOPE_ADDED:
        return f"➕ <b>Scope added</b>\n     <code>{escape_html(key)}</code>"
    if key and t == ChangeType.SCOPE_REMOVED:
        return f"➖ <b>Scope removed</b>\n     <code>{escape_html(key)}</code>"
    if key:  # any other scope-level change (severity, eligibility, instruction…)
        head = f"✏️ <b>Scope changed</b>\n     <code>{escape_html(key)}</code>"
        rows = []
        for field, pair in (d.get("fields") or {}).items():
            a, b = pair
            rows.append(
                f"     {escape_html(field)}: "
                + _TRANSITION.format(a=escape_html(str(a)), b=escape_html(str(b)))
            )
        return head + ("\n" + "\n".join(rows) if rows else "")

    if t == ChangeType.BOUNTY_CHANGED and "offers_bounties_to" in d:
        a = "on" if d.get("offers_bounties_from") else "off"
        b = "on" if d.get("offers_bounties_to") else "off"
        return "💰 <b>Bounties</b> " + _TRANSITION.format(a=a, b=b)
    if t == ChangeType.PROGRAM_STATE and "submission_state_to" in d:
        return "⏸ <b>State</b> " + _TRANSITION.format(
            a=escape_html(str(d.get("submission_state_from"))),
            b=escape_html(str(d.get("submission_state_to"))),
        )
    if t == ChangeType.PROGRAM_STATE and d.get("policy_changed"):
        return "📝 <b>Policy text changed</b>"
    if t == ChangeType.PROGRAM_ADDED:
        return "➕ <b>Now accessible to you</b>"
    if t == ChangeType.PROGRAM_REMOVED:
        return "➖ <b>No longer accessible</b>"
    return f"• {escape_html(c.summary)}"


def _header(c: Change) -> str:
    return f"🎯 <b>{escape_html(c.program_name)}</b> ({escape_html(c.program_handle)})\n{_link(_program_url(c))}"


def format_group_messages(group: list[Change]) -> list[str]:
    """Render one program's changes as one (or more, if long) Telegram messages:
    a single header + clickable URL, then a styled line per change."""
    if len(group) == 1 and ChangeType.NEW_PUBLIC_PROGRAM in group[0].types:
        return [_format_new_program(group[0])]

    header = _header(group[0])
    msgs: list[str] = []
    cur = header
    for c in group:
        line = _change_line(c)
        sep = "\n\n" if cur == header else "\n"
        if len(cur) + len(sep) + len(line) > _MAX:
            msgs.append(cur)
            cur = header + "\n\n" + line
        else:
            cur += sep + line
    msgs.append(cur)
    return msgs


def format_change(c: Change) -> str:
    """Single-change message (used for one-off formatting/tests)."""
    return format_group_messages([c])[0]


class Notifier:
    def __init__(self, bot, chat_id: int):
        self._bot = bot
        self._chat_id = chat_id

    async def send_text(self, text: str) -> None:
        await self._bot.send_message(
            self._chat_id, text, parse_mode="HTML", disable_web_page_preview=True
        )

    async def send_changes(self, changes: list[Change]) -> None:
        groups: dict[str, list[Change]] = {}
        for c in changes:
            groups.setdefault(c.program_handle, []).append(c)
        for group in groups.values():
            for msg in format_group_messages(group):
                await self.send_text(msg)
