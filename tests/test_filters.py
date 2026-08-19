from h1monitor.filters import filter_changes
from h1monitor.models import Change, Preferences, ChangeType


def _c(types, handle="acme", state="open", details=None):
    return Change(frozenset(types), handle, handle.title(), state, "s", details or {})


def test_type_toggle_off_drops():
    p = Preferences.defaults()
    p.enabled[ChangeType.SCOPE_ADDED] = False
    assert filter_changes([_c({ChangeType.SCOPE_ADDED})], p) == []


def test_dual_tag_kept_if_any_enabled():
    p = Preferences.defaults()
    p.enabled[ChangeType.SCOPE_MODIFIED] = False
    kept = filter_changes([_c({ChangeType.SCOPE_MODIFIED, ChangeType.BOUNTY_CHANGED})], p)
    assert len(kept) == 1  # BOUNTY_CHANGED still on


def test_denylist_drops_non_directory():
    p = Preferences.defaults()
    p.denylist = frozenset({"acme"})
    assert filter_changes([_c({ChangeType.SCOPE_ADDED}, handle="acme")], p) == []


def test_allowlist_only():
    p = Preferences.defaults()
    p.allowlist = frozenset({"beta"})
    got = filter_changes(
        [
            _c({ChangeType.SCOPE_ADDED}, handle="acme"),
            _c({ChangeType.SCOPE_ADDED}, handle="beta"),
        ],
        p,
    )
    assert [c.program_handle for c in got] == ["beta"]


def test_new_public_program_ignores_allow_deny():
    p = Preferences.defaults()
    p.denylist = frozenset({"vercel"})
    got = filter_changes([_c({ChangeType.NEW_PUBLIC_PROGRAM}, handle="vercel")], p)
    assert len(got) == 1


def test_exclude_paused_drops():
    p = Preferences.defaults()
    assert filter_changes([_c({ChangeType.SCOPE_ADDED}, state="paused")], p) == []


def test_exclude_paused_keeps_became_paused_transition():
    p = Preferences.defaults()
    c = _c({ChangeType.PROGRAM_STATE}, state="paused", details={"became_paused": True})
    assert len(filter_changes([c], p)) == 1


def test_exclude_paused_off_keeps():
    p = Preferences.defaults()
    p.exclude_paused = False
    assert len(filter_changes([_c({ChangeType.SCOPE_ADDED}, state="paused")], p)) == 1
