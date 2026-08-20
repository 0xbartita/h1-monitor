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


def test_scope_change_format_has_name_and_asset():
    c = Change(
        frozenset({ChangeType.SCOPE_ADDED}), "acme", "Acme", "open",
        "scope added: URL:a.com", {"scope_key": "URL:a.com"},
    )
    text = format_change(c)
    assert "Acme" in text
    assert "(acme)" not in text  # redundant handle-in-parens removed; URL carries it
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


def test_split_for_telegram_bounds_every_chunk():
    from h1monitor.notifier import split_for_telegram
    text = "\n".join(f"line {i}" for i in range(2000))
    chunks = split_for_telegram(text, limit=500)
    assert all(len(c) <= 500 for c in chunks)
    assert sum(c.count("line ") for c in chunks) == 2000  # no lines lost


def test_oversized_scope_change_stays_under_telegram_limit():
    from h1monitor.notifier import format_group_messages, split_for_telegram
    huge = "x" * 20000  # a multi-KB instruction value
    c = Change(
        frozenset({ChangeType.SCOPE_MODIFIED}), "acme", "Acme", "open",
        "Scope changed",
        {"scope_key": "URL:acme.com", "fields": {"instruction": (huge, huge)}},
    )
    msgs = []
    for m in format_group_messages([c]):
        msgs.extend(split_for_telegram(m))
    assert msgs and all(len(m) <= 4096 for m in msgs)  # never exceeds hard limit
    assert any("…" in m for m in msgs)                  # value was clipped
    assert all(huge not in m for m in msgs)             # raw giant value not emitted


def test_cleared_scope_field_reads_plainly_not_python_none():
    """When a scope field is cleared (e.g. t.co's instruction removed), the alert
    must not leak Python's 'None'. Booleans and text values stay untouched."""
    c = Change(
        frozenset({ChangeType.SCOPE_MODIFIED}), "x", "X / xAI", "open",
        "Scope changed",
        {"scope_key": "URL:t.co",
         "fields": {"instruction": ("do not report t.co issues", None),
                    "eligible_for_bounty": (False, True)}},
    )
    text = format_change(c)
    assert "<code>None</code>" not in text   # no raw Python repr in the message
    assert "(none)" in text                   # cleared value shown cleanly
    assert "False → True" in text.replace("<code>", "").replace("</code>", "")


def test_scope_field_set_from_empty_reads_plainly():
    """A field gaining a value (was empty) also reads cleanly on the old side."""
    c = Change(
        frozenset({ChangeType.SCOPE_MODIFIED}), "x", "X / xAI", "open",
        "Scope changed",
        {"scope_key": "URL:x.ai", "fields": {"instruction": (None, "test in prod")}},
    )
    text = format_change(c)
    assert "<code>None</code>" not in text
    assert "(none)" in text and "test in prod" in text


@pytest.mark.asyncio
async def test_send_changes_survives_a_failing_message():
    from h1monitor.notifier import Notifier
    calls = {"n": 0}

    class FlakyBot:
        async def send_message(self, chat, text, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("telegram 500")

    n = Notifier(FlakyBot(), 42)
    changes = [
        Change(frozenset({ChangeType.PROGRAM_ADDED}), "a", "A", "open", "s", {}),
        Change(frozenset({ChangeType.PROGRAM_ADDED}), "b", "B", "open", "s", {}),
    ]
    await n.send_changes(changes)   # must not raise despite the first send failing
    assert calls["n"] == 2          # continued to the second program
