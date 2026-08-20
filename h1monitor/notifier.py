from __future__ import annotations

import logging
from html import escape

from h1monitor.models import Change, ChangeType

log = logging.getLogger("h1monitor")

_MAX = 3800            # soft budget for packing several change lines per message
_TG_LIMIT = 4096       # Telegram's hard per-message character limit
_MAX_FIELD = 300       # cap a single old/new field value so one line can't blow up


def escape_html(s: str) -> str:
    return escape(s or "")


def _clip(s: str, n: int = _MAX_FIELD) -> str:
    """Bound a raw field value so an alert line stays well under the TG limit."""
    s = s or ""
    return s if len(s) <= n else s[:n].rstrip() + "…"


# Human labels for HackerOne's raw scope-field keys (see differ._SCOPE_FIELDS).
_FIELD_LABELS = {
    "eligible_for_bounty": "Bounty eligible",
    "eligible_for_submission": "Submission eligible",
    "max_severity": "Max severity",
    "instruction": "Instruction",
    "confidentiality_requirement": "Confidentiality requirement",
    "integrity_requirement": "Integrity requirement",
    "availability_requirement": "Availability requirement",
}


def _field_label(field: str) -> str:
    """Readable name for a scope field. Unmapped keys fall back to a
    de-underscored, sentence-cased form (e.g. 'some_new_field' → 'Some new field')."""
    return _FIELD_LABELS.get(field) or field.replace("_", " ").capitalize()


def _field_value(v) -> str:
    """Render one old/new field value for an alert. Booleans read as yes/no; a
    cleared value (null or empty) reads as a plain '(none)' instead of leaking
    Python's 'None'; other values are shown as-is (clipped if huge)."""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if v is None or v == "":
        return "(none)"
    return _clip(str(v))


def split_for_telegram(text: str, limit: int = _TG_LIMIT) -> list[str]:
    """Split a message into <=limit-char chunks on line boundaries (each of our
    lines carries balanced HTML tags, so newline splits stay valid). A lone line
    longer than the limit is hard-sliced as a last resort."""
    if len(text) <= limit:
        return [text]
    out: list[str] = []
    cur = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if cur:
                out.append(cur)
                cur = ""
            out.append(line[:limit])
            line = line[limit:]
        piece = line if not cur else cur + "\n" + line
        if len(piece) > limit:
            out.append(cur)
            cur = line
        else:
            cur = piece
    if cur:
        out.append(cur)
    return out


def describe_error(e: BaseException) -> str:
    """Human-readable error text. Some httpx transport errors (ReadError,
    ConnectError, …) stringify to "", which would make an alert read
    "…failed:" with nothing after it — fall back to the exception type."""
    return str(e).strip() or e.__class__.__name__


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


def _scope_key_html(key: str) -> str:
    """Render a scope key so ONLY the asset is the copyable monospace span. The
    'URL:'/'WILDCARD:'/… type prefix stays plain text beside it, so a tap-to-copy
    yields a clean asset (myaccount.example.com) with no 'URL:' noise attached."""
    kind, sep, ident = key.partition(":")  # split on the first ':' only
    if not sep:  # no type prefix — the whole key is the asset
        return f"<code>{escape_html(key)}</code>"
    return f"{escape_html(kind)}: <code>{escape_html(ident)}</code>"


def _change_line(c: Change) -> str:
    """One styled line describing a single change, technical bits in monospace."""
    t = c.primary_type
    d = c.details or {}
    key = d.get("scope_key")

    if key and t == ChangeType.SCOPE_ADDED:
        return f"➕ <b>Scope added</b>\n     {_scope_key_html(key)}"
    if key and t == ChangeType.SCOPE_REMOVED:
        return f"➖ <b>Scope removed</b>\n     {_scope_key_html(key)}"
    if key:  # any other scope-level change (severity, eligibility, instruction…)
        head = f"✏️ <b>Scope changed</b>\n     {_scope_key_html(key)}"
        rows = []
        for field, pair in (d.get("fields") or {}).items():
            a, b = pair
            rows.append(
                f"     {escape_html(_field_label(field))}: "
                + _TRANSITION.format(
                    a=escape_html(_field_value(a)), b=escape_html(_field_value(b))
                )
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
    return f"🎯 <b>{escape_html(c.program_name)}</b>\n{_link(_program_url(c))}"


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

    async def send_text(self, text: str) -> bool:
        """Deliver a message (splitting if oversized). Never raises — returns
        False if delivery failed so callers can react without crashing."""
        try:
            for chunk in split_for_telegram(text):
                await self._bot.send_message(
                    self._chat_id, chunk, parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            return True
        except Exception:  # noqa: BLE001 — a send failure must not kill the caller
            log.warning("Failed to send Telegram message", exc_info=True)
            return False

    async def send_changes(self, changes: list[Change]) -> None:
        groups: dict[str, list[Change]] = {}
        for c in changes:
            groups.setdefault(c.program_handle, []).append(c)
        for group in groups.values():
            for msg in format_group_messages(group):
                await self.send_text(msg)  # never raises; one bad msg won't abort
