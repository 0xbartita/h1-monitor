from __future__ import annotations

from html import escape

from h1monitor.models import Change, ChangeType

_MAX = 3800


def escape_html(s: str) -> str:
    return escape(s or "")


def _format_new_program(c: Change) -> str:
    dp = c.directory
    kind = (
        "Bug bounty program"
        if (dp and dp.offers_bounties)
        else "vulnerability disclosure program"
    )
    name = escape_html(dp.name if dp else c.program_name)
    if dp and dp.started_accepting_at:
        body = (
            f"<b>{name}</b> launched on {escape_html(dp.started_accepting_at)} "
            f"as a {kind}."
        )
    else:
        body = f"<b>{name}</b> was newly observed as a {kind}."
    url = dp.url if dp and dp.url else f"https://hackerone.com/{c.program_handle}"
    return (
        f"🆕 New Program: {name}\n{body}\n"
        f'<a href="{escape_html(url)}">Open program</a>'
    )


def format_change(c: Change) -> str:
    if ChangeType.NEW_PUBLIC_PROGRAM in c.types:
        return _format_new_program(c)
    label = c.category.value
    return (
        f"{label}\n<b>{escape_html(c.program_name)}</b> "
        f"({escape_html(c.program_handle)})\n{escape_html(c.summary)}"
    )


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
        for handle, group in groups.items():
            buf = ""
            for c in group:
                block = format_change(c)
                if buf and len(buf) + len(block) + 2 > _MAX:
                    await self.send_text(buf)
                    buf = ""
                buf = f"{buf}\n\n{block}" if buf else block
            if buf:
                await self.send_text(buf)
