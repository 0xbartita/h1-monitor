from h1monitor.models import (
    ChangeType, Category, CATEGORY_FOR_TYPE, Scope, Program, DirectoryProgram,
    Snapshot, Change, Preferences,
)


def test_scope_key():
    s = Scope("URL", "example.com", True, True, "critical", None, None, None, None, None)
    assert s.key == "URL:example.com"


def test_every_change_type_has_category():
    for t in ChangeType:
        assert t in CATEGORY_FOR_TYPE
        assert isinstance(CATEGORY_FOR_TYPE[t], Category)


def test_change_primary_type_is_declaration_order():
    c = Change(
        types=frozenset({ChangeType.BOUNTY_CHANGED, ChangeType.SCOPE_MODIFIED}),
        program_handle="acme", program_name="Acme", submission_state="open",
        summary="x", details={},
    )
    # SCOPE_MODIFIED declared before BOUNTY_CHANGED
    assert c.primary_type == ChangeType.SCOPE_MODIFIED
    assert c.category == CATEGORY_FOR_TYPE[ChangeType.SCOPE_MODIFIED]


def test_preferences_defaults_all_on():
    p = Preferences.defaults()
    assert all(p.is_type_enabled(t) for t in ChangeType)
    assert p.exclude_paused is True
    assert p.poll_interval_minutes == 30


def test_preferences_json_roundtrip():
    p = Preferences.defaults()
    p.enabled[ChangeType.SCOPE_ADDED] = False
    p.allowlist = frozenset({"acme"})
    p2 = Preferences.from_json(p.to_json())
    assert p2.is_type_enabled(ChangeType.SCOPE_ADDED) is False
    assert p2.allowlist == frozenset({"acme"})
    assert p2.exclude_paused is True
