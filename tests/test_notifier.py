import pytest
from datetime import date as _date, timedelta

from h1monitor.notifier import (
    format_change, format_group_messages, Notifier, _launch_is_recent,
)
from h1monitor.models import Change, ChangeType, DirectoryProgram

_RECENT = (_date.today() - timedelta(days=3)).isoformat()   # genuinely just launched
_OLD_LAUNCH = "2017-05-02"                                  # established years ago


def _dir_change(date=_RECENT, bounties=True):
    dp = DirectoryProgram(
        "vercel", "Vercel Sandbox", bounties, "open", date,
        "https://hackerone.com/vercel",
    )
    return Change(
        frozenset({ChangeType.NEW_PUBLIC_PROGRAM}), "vercel", "Vercel Sandbox",
        "open", "New public program", {}, directory=dp, source="public",
    )


def test_recent_launch_says_new_program_with_date():
    text = format_change(_dir_change())  # recent launch
    assert "New Program: Vercel Sandbox" in text
    assert f"launched on {_RECENT} as a Bug bounty program" in text
    assert "hackerone.com/vercel" in text


def test_old_program_says_now_listed_not_new():
    # An established program (launched years ago) that just appears in the public
    # directory is "Now listed", not a "New Program".
    text = format_change(_dir_change(date=_OLD_LAUNCH))
    assert "Now listed: Vercel Sandbox" in text
    assert "New Program" not in text
    assert _OLD_LAUNCH in text          # its real launch date is still shown
    assert "🌐 Public" in text          # badge still present


def test_dateless_program_says_now_listed():
    # No launch date → we can't confirm it's fresh, so don't call it "New".
    text = format_change(_dir_change(date=None))
    assert "Now listed: Vercel Sandbox" in text
    assert "New Program" not in text
    assert "is now listed in the public directory as a Bug bounty program" in text


def test_vdp_wording():
    assert "vulnerability disclosure program" in format_change(_dir_change(bounties=False))


def test_new_public_program_header_tagged_public():
    # A directory launch is always public — its 🆕 header carries the 🌐 Public tag.
    assert "🌐 Public" in format_change(_dir_change())


def test_launch_is_recent_true_for_recent_date():
    assert _launch_is_recent("2026-08-01", _date(2026, 8, 21)) is True


def test_launch_is_recent_false_for_old_date():
    assert _launch_is_recent("2017-05-02", _date(2026, 8, 21)) is False


def test_launch_is_recent_false_for_missing_or_bad_date():
    today = _date(2026, 8, 21)
    assert _launch_is_recent(None, today) is False
    assert _launch_is_recent("", today) is False
    assert _launch_is_recent("not-a-date", today) is False


def test_private_program_header_shows_private_badge():
    c = Change(
        frozenset({ChangeType.SCOPE_ADDED}), "acme", "Acme", "open",
        "scope added", {"scope_key": "URL:a.com"}, source="private",
    )
    text = format_change(c)
    # Badge rides on the name line, right after the program name, before the link.
    assert "🎯 <b>Acme</b> · 🔒 Private" in text
    assert "🌐" not in text


def test_public_program_header_shows_public_badge():
    c = Change(
        frozenset({ChangeType.SCOPE_ADDED}), "vercel", "Vercel", "open",
        "scope added", {"scope_key": "URL:a.com"}, source="public",
    )
    text = format_change(c)
    assert "🎯 <b>Vercel</b> · 🌐 Public" in text
    assert "🔒" not in text


def test_header_without_source_shows_no_badge():
    """Defensive: a change with no source stamped renders neither badge, and no
    dangling ' · ' separator — the name line stays exactly as before."""
    c = Change(
        frozenset({ChangeType.SCOPE_ADDED}), "acme", "Acme", "open",
        "scope added", {"scope_key": "URL:a.com"},
    )
    text = format_change(c)
    assert "🔒" not in text and "🌐" not in text
    assert "Acme</b> ·" not in text


def test_out_of_scope_added_scope_reads_as_updated():
    # An asset that turns up out of scope isn't a new target — the program moved
    # it out of coverage, so the line must say "updated", never "added".
    c = Change(
        frozenset({ChangeType.SCOPE_ADDED}), "workforce", "Workforce.com", "open",
        "scope added", {"scope_key": "AI_MODEL:Roster Agent",
                        "eligible_for_submission": False},
    )
    text = format_change(c)
    assert "Scope updated" in text
    assert "Scope added" not in text
    assert "➕" not in text  # a plus sign would still read as a new target
    assert "out of scope" in text
    assert "<code>Roster Agent</code>" in text  # asset still tap-to-copy


def test_in_scope_added_scope_has_no_coverage_tag():
    # In scope is the norm (and every public addition is in scope) — stays clean.
    c = Change(
        frozenset({ChangeType.SCOPE_ADDED}), "x", "xAI", "open",
        "scope added", {"scope_key": "URL:console.x.ai",
                        "eligible_for_submission": True},
    )
    text = format_change(c)
    assert "Scope added" in text
    assert "out of scope" not in text and "in scope" not in text


def test_added_scope_without_coverage_key_renders_clean():
    # Backward-compatible: a details dict lacking the flag shows no tag, no crash.
    c = Change(
        frozenset({ChangeType.SCOPE_ADDED}), "acme", "Acme", "open",
        "scope added", {"scope_key": "URL:a.com"},
    )
    assert "out of scope" not in format_change(c)


def test_scope_change_format_has_name_and_asset():
    c = Change(
        frozenset({ChangeType.SCOPE_ADDED}), "acme", "Acme", "open",
        "scope added: URL:a.com", {"scope_key": "URL:a.com"},
    )
    text = format_change(c)
    assert "Acme" in text
    assert "(acme)" not in text  # redundant handle-in-parens removed; URL carries it
    # only the asset is copyable; the 'URL:' type prefix stays out of the code span
    assert "Scope added" in text and "<code>a.com</code>" in text and "URL:" in text


def test_scope_asset_is_copyable_without_the_type_prefix():
    """Tap-to-copy must yield a clean asset. Only the identifier sits in the
    <code> span; the 'URL:' type prefix stays plain text beside it."""
    c = Change(
        frozenset({ChangeType.SCOPE_ADDED}), "payoneer", "Payoneer", "open",
        "scope added", {"scope_key": "URL:myaccount.sandbox.payoneer.com"},
    )
    text = format_change(c)
    assert "<code>myaccount.sandbox.payoneer.com</code>" in text  # asset alone is copyable
    assert "<code>URL:" not in text                               # prefix never inside the span
    assert "URL:" in text.replace("<code>", "").replace("</code>", "")  # type still visible


def test_wildcard_scope_copies_just_the_pattern():
    c = Change(
        frozenset({ChangeType.SCOPE_REMOVED}), "acme", "Acme", "open",
        "scope removed", {"scope_key": "WILDCARD:*.a2verify.com"},
    )
    text = format_change(c)
    assert "<code>*.a2verify.com</code>" in text
    assert "<code>WILDCARD:" not in text


def test_scope_key_without_a_type_prefix_is_fully_copyable():
    """Defensive: a key with no 'TYPE:' prefix still renders as one code span."""
    c = Change(
        frozenset({ChangeType.SCOPE_ADDED}), "acme", "Acme", "open",
        "scope added", {"scope_key": "bareidentifier"},
    )
    assert "<code>bareidentifier</code>" in format_change(c)


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
    assert "<code>a.com</code>" in text and "<code>b.com</code>" in text


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
    plain = text.replace("<code>", "").replace("</code>", "")
    assert "Bounty eligible: no → yes" in plain  # humanized name + yes/no


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


def test_scope_field_names_are_humanized():
    """Raw API field names and booleans read like English, not code:
    'eligible_for_bounty: False → True' becomes 'Bounty eligible: no → yes'."""
    c = Change(
        frozenset({ChangeType.SCOPE_MODIFIED}), "acme", "Acme", "open",
        "Scope changed",
        {"scope_key": "URL:a.com",
         "fields": {
             "eligible_for_submission": (True, False),
             "max_severity": ("high", "critical"),
         }},
    )
    plain = format_change(c).replace("<code>", "").replace("</code>", "")
    assert "eligible_for_submission" not in plain
    assert "Submission eligible: yes → no" in plain
    assert "Max severity: high → critical" in plain


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
