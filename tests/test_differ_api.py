from h1monitor.differ import diff_api
from h1monitor.models import Snapshot, Program, Scope, ChangeType


def _prog(handle="acme", state="open", bounties=True, scopes=None, policy="p"):
    return Program(handle, handle.title(), state, bounties, "USD", policy, scopes or {})


def _scope(ident="a.com", sev="high", bounty=True, submit=True, instr="x"):
    return Scope("URL", ident, bounty, submit, sev, instr, None, None, None, "t")


def test_first_run_is_silent():
    assert diff_api(None, Snapshot({"acme": _prog()})) == []


def test_no_changes():
    s = Snapshot({"acme": _prog(scopes={"URL:a.com": _scope()})})
    assert diff_api(s, Snapshot({"acme": _prog(scopes={"URL:a.com": _scope()})})) == []


def test_scope_added():
    prev = Snapshot({"acme": _prog(scopes={})})
    curr = Snapshot({"acme": _prog(scopes={"URL:a.com": _scope()})})
    changes = diff_api(prev, curr)
    assert any(ChangeType.SCOPE_ADDED in c.types for c in changes)


def test_api_changes_are_tagged_private():
    # Every change from the private API diff must carry source="private",
    # so the alert header can render the 🔒 Private badge.
    prev = Snapshot({"acme": _prog(scopes={})})
    curr = Snapshot({"acme": _prog(scopes={"URL:a.com": _scope()})})
    changes = diff_api(prev, curr)
    assert changes and all(c.source == "private" for c in changes)


def test_scope_added_records_out_of_scope_coverage():
    # An asset added out of scope (eligible_for_submission=False) must record it,
    # so the alert can flag "out of scope" instead of implying a new target.
    prev = Snapshot({"acme": _prog(scopes={})})
    curr = Snapshot({"acme": _prog(scopes={"URL:a.com": _scope(submit=False)})})
    c = [c for c in diff_api(prev, curr) if ChangeType.SCOPE_ADDED in c.types][0]
    assert c.details["eligible_for_submission"] is False


def test_scope_added_records_in_scope_coverage():
    prev = Snapshot({"acme": _prog(scopes={})})
    curr = Snapshot({"acme": _prog(scopes={"URL:a.com": _scope(submit=True)})})
    c = [c for c in diff_api(prev, curr) if ChangeType.SCOPE_ADDED in c.types][0]
    assert c.details["eligible_for_submission"] is True


def test_scope_removed():
    prev = Snapshot({"acme": _prog(scopes={"URL:a.com": _scope()})})
    curr = Snapshot({"acme": _prog(scopes={})})
    assert any(ChangeType.SCOPE_REMOVED in c.types for c in diff_api(prev, curr))


def test_scope_modified_severity_tags_both_types():
    prev = Snapshot({"acme": _prog(scopes={"URL:a.com": _scope(sev="low")})})
    curr = Snapshot({"acme": _prog(scopes={"URL:a.com": _scope(sev="critical")})})
    c = [c for c in diff_api(prev, curr) if c.details.get("scope_key") == "URL:a.com"][0]
    assert ChangeType.SCOPE_MODIFIED in c.types
    assert ChangeType.BOUNTY_CHANGED in c.types  # max_severity dual-tag


def test_bounty_eligibility_toggle():
    prev = Snapshot({"acme": _prog(scopes={"URL:a.com": _scope(bounty=False)})})
    curr = Snapshot({"acme": _prog(scopes={"URL:a.com": _scope(bounty=True)})})
    assert any(ChangeType.BOUNTY_CHANGED in c.types for c in diff_api(prev, curr))


def test_program_offers_bounties_toggle():
    prev = Snapshot({"acme": _prog(bounties=False)})
    curr = Snapshot({"acme": _prog(bounties=True)})
    assert any(ChangeType.BOUNTY_CHANGED in c.types for c in diff_api(prev, curr))


def test_program_added_and_removed():
    prev = Snapshot({"acme": _prog()})
    curr = Snapshot({"beta": _prog(handle="beta")})
    types = {t for c in diff_api(prev, curr) for t in c.types}
    assert ChangeType.PROGRAM_ADDED in types
    assert ChangeType.PROGRAM_REMOVED in types


def test_program_state_became_paused_flag():
    prev = Snapshot({"acme": _prog(state="open")})
    curr = Snapshot({"acme": _prog(state="paused")})
    c = [c for c in diff_api(prev, curr) if ChangeType.PROGRAM_STATE in c.types][0]
    assert c.details["became_paused"] is True
    assert c.submission_state == "paused"


def test_policy_change_has_its_own_type():
    # Separate from PROGRAM_STATE so /config can silence "the rules changed"
    # without also silencing "the program paused", and the other way round.
    prev = Snapshot({"acme": _prog(policy="old")})
    curr = Snapshot({"acme": _prog(policy="new")})
    changes = diff_api(prev, curr)
    assert any(ChangeType.POLICY_CHANGED in c.types for c in changes)
    assert not any(ChangeType.PROGRAM_STATE in c.types for c in changes)
