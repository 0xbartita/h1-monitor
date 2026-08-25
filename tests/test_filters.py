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


# --- pause and policy are switched independently ----------------------------

def _pause_change():
    return Change(frozenset({ChangeType.PROGRAM_STATE}), "acme", "Acme", "paused",
                  "state: open → paused",
                  {"submission_state_from": "open", "submission_state_to": "paused",
                   "became_paused": True})


def _policy_change():
    return Change(frozenset({ChangeType.POLICY_CHANGED}), "acme", "Acme", "open",
                  "policy text changed",
                  {"policy_changed": True, "became_paused": False})


def test_turning_off_pause_alerts_keeps_policy_alerts():
    p = Preferences.defaults()
    p.enabled[ChangeType.PROGRAM_STATE] = False
    assert filter_changes([_pause_change()], p) == []
    assert len(filter_changes([_policy_change()], p)) == 1


def test_turning_off_policy_alerts_keeps_pause_alerts():
    p = Preferences.defaults()
    p.enabled[ChangeType.POLICY_CHANGED] = False
    assert filter_changes([_policy_change()], p) == []
    assert len(filter_changes([_pause_change()], p)) == 1


def test_both_on_by_default():
    p = Preferences.defaults()
    assert len(filter_changes([_pause_change(), _policy_change()], p)) == 2


def test_preferences_saved_before_the_split_default_policy_to_on():
    # An older stored preferences blob has no policy_changed key; a missing type
    # must default to enabled rather than silently swallowing the new alert.
    old = '{"enabled": {"program_state": true}, "exclude_paused": true}'
    p = Preferences.from_json(old)
    assert p.is_type_enabled(ChangeType.POLICY_CHANGED) is True
