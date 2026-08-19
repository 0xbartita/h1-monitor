import pytest

from h1monitor.notifier import format_change, Notifier
from h1monitor.models import Change, ChangeType, DirectoryProgram


def _dir_change(date="2026-08-18", bounties=True):
    dp = DirectoryProgram(
        "vercel", "Vercel Sandbox", bounties, "open", date,
        "https://hackerone.com/vercel",
    )
    return Change(
        frozenset({ChangeType.NEW_PUBLIC_PROGRAM}), "vercel", "Vercel Sandbox",
        "open", "New public program", {}, directory=dp,
    )


def test_new_program_format_with_date():
    text = format_change(_dir_change())
    assert "🆕 New Program: Vercel Sandbox" in text
    assert "launched on 2026-08-18 as a Bug bounty program" in text
    assert "hackerone.com/vercel" in text


def test_new_program_format_without_date():
    text = format_change(_dir_change(date=None))
    assert "was newly observed as a Bug bounty program" in text


def test_vdp_wording():
    assert "vulnerability disclosure program" in format_change(_dir_change(bounties=False))


def test_scope_change_format_has_category_and_handle():
    c = Change(
        frozenset({ChangeType.SCOPE_ADDED}), "acme", "Acme", "open",
        "scope added: URL:a.com", {"scope_key": "URL:a.com"},
    )
    text = format_change(c)
    assert "🎯 Scope Change" in text and "acme" in text and "URL:a.com" in text


@pytest.mark.asyncio
async def test_notifier_groups_and_sends():
    sent = []

    class FakeBot:
        async def send_message(self, chat_id, text, **kw):
            sent.append((chat_id, text))

    n = Notifier(FakeBot(), 42)
    await n.send_changes(
        [
            Change(frozenset({ChangeType.SCOPE_ADDED}), "acme", "Acme", "open", "a", {}),
            Change(frozenset({ChangeType.SCOPE_REMOVED}), "acme", "Acme", "open", "b", {}),
            Change(frozenset({ChangeType.SCOPE_ADDED}), "beta", "Beta", "open", "c", {}),
        ]
    )
    assert len(sent) == 2  # grouped by handle: acme, beta
    assert all(m[0] == 42 for m in sent)
