import pytest

from h1monitor.notifier import format_change, format_group_messages, Notifier
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
    assert "New Program: Vercel Sandbox" in text
    assert "launched on 2026-08-18 as a Bug bounty program" in text
    assert "hackerone.com/vercel" in text


def test_new_program_format_without_date():
    text = format_change(_dir_change(date=None))
    assert "was newly observed as a Bug bounty program" in text


def test_vdp_wording():
    assert "vulnerability disclosure program" in format_change(_dir_change(bounties=False))


def test_scope_change_format_has_name_handle_and_asset():
    c = Change(
        frozenset({ChangeType.SCOPE_ADDED}), "acme", "Acme", "open",
        "scope added: URL:a.com", {"scope_key": "URL:a.com"},
    )
    text = format_change(c)
    assert "Acme" in text and "(acme)" in text
    assert "Scope added" in text and "URL:a.com" in text


def test_scope_change_includes_clickable_program_url():
    c = Change(
        frozenset({ChangeType.SCOPE_ADDED}), "equity_residential", "Equity Residential",
        "open", "scope added: URL:contact.example.com", {"scope_key": "URL:contact.example.com"},
    )
    text = format_change(c)
    assert 'href="https://hackerone.com/equity_residential"' in text
    assert "hackerone.com/equity_residential" in text  # visible link text


def test_group_shows_one_header_for_multiple_scopes():
    changes = [
        Change(frozenset({ChangeType.SCOPE_ADDED}), "acme", "Acme", "open",
               "scope added: URL:a.com", {"scope_key": "URL:a.com"}),
        Change(frozenset({ChangeType.SCOPE_ADDED}), "acme", "Acme", "open",
               "scope added: URL:b.com", {"scope_key": "URL:b.com"}),
    ]
    msgs = format_group_messages(changes)
    assert len(msgs) == 1
    text = msgs[0]
    assert text.count('href="https://hackerone.com/acme"') == 1  # one header, not repeated
    assert "URL:a.com" in text and "URL:b.com" in text


def test_bounty_and_state_transitions_styled():
    b = Change(frozenset({ChangeType.BOUNTY_CHANGED}), "acme", "Acme", "open",
               "offers_bounties: False → True",
               {"offers_bounties_from": False, "offers_bounties_to": True})
    assert "Bounties" in format_change(b) and "off" in format_change(b) and "on" in format_change(b)
    s = Change(frozenset({ChangeType.PROGRAM_STATE}), "acme", "Acme", "paused",
               "state: open → paused",
               {"submission_state_from": "open", "submission_state_to": "paused"})
    st = format_change(s)
    assert "State" in st and "open" in st and "paused" in st


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


def test_describe_error_falls_back_to_type_name_when_empty():
    import httpx
    from h1monitor.notifier import describe_error
    # httpx transport errors stringify to "" — must not yield an empty alert
    assert describe_error(httpx.ReadError("")) == "ReadError"
    assert describe_error(RuntimeError("boom")) == "boom"
