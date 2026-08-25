from h1monitor.differ import diff_snapshot
from h1monitor.models import Snapshot, Program, ChangeType


def _pub(handle, date="2026-08-18", bounties=True):
    return Program(handle, handle.title(), "open", bounties, None, None, {}, date)


def test_first_run_silent():
    curr = Snapshot({"a": _pub("a"), "b": _pub("b")})
    assert diff_snapshot(None, curr, ChangeType.NEW_PUBLIC_PROGRAM) == []


def test_new_public_program_detected_with_launch_date():
    prev = Snapshot({"a": _pub("a")})
    curr = Snapshot({"a": _pub("a"), "vercel": _pub("vercel", date="2026-08-18")})
    changes = diff_snapshot(prev, curr, ChangeType.NEW_PUBLIC_PROGRAM)
    assert len(changes) == 1
    c = changes[0]
    assert ChangeType.NEW_PUBLIC_PROGRAM in c.types
    assert c.program_handle == "vercel"
    assert c.directory.started_accepting_at == "2026-08-18"
    assert c.directory.url == "https://hackerone.com/vercel"


def test_no_new_programs():
    prev = Snapshot({"a": _pub("a"), "b": _pub("b")})
    curr = Snapshot({"a": _pub("a"), "b": _pub("b")})
    assert diff_snapshot(prev, curr, ChangeType.NEW_PUBLIC_PROGRAM) == []


def test_directory_changes_are_tagged_public():
    # Every change from the public directory diff must carry source="public",
    # so the alert header can render the 🌐 Public badge.
    prev = Snapshot({"a": _pub("a")})
    curr = Snapshot({"a": _pub("a"), "vercel": _pub("vercel")})
    changes = diff_snapshot(prev, curr, ChangeType.NEW_PUBLIC_PROGRAM)
    assert changes and all(c.source == "public" for c in changes)
