from __future__ import annotations

from h1monitor.models import (
    Snapshot, Program, Scope, Change, ChangeType, DirectoryProgram,
)

_SCOPE_FIELDS = [
    "max_severity",
    "instruction",
    "eligible_for_submission",
    "confidentiality_requirement",
    "integrity_requirement",
    "availability_requirement",
]


def _mk(types, prog: Program, summary: str, details: dict) -> Change:
    return Change(
        frozenset(types), prog.handle, prog.name, prog.submission_state, summary, details
    )


def _added_change(p: Program, new_program_type: ChangeType) -> Change:
    if new_program_type == ChangeType.NEW_PUBLIC_PROGRAM:
        dp = DirectoryProgram(
            p.handle, p.name, bool(p.offers_bounties), p.submission_state,
            p.started_accepting_at, f"https://hackerone.com/{p.handle}",
        )
        return Change(
            frozenset({ChangeType.NEW_PUBLIC_PROGRAM}), p.handle, p.name,
            p.submission_state, f"New public program: {p.name}",
            {"started_accepting_at": p.started_accepting_at}, directory=dp,
        )
    return _mk({new_program_type}, p, f"{p.name} is now accessible to you", {})


def diff_snapshot(
    prev: Snapshot | None,
    curr: Snapshot,
    new_program_type: ChangeType = ChangeType.PROGRAM_ADDED,
) -> list[Change]:
    """Diff two program snapshots. Newly-appeared programs are tagged with
    `new_program_type` (NEW_PUBLIC_PROGRAM for the public directory source,
    PROGRAM_ADDED for the private API source). First run (prev is None) is
    silent."""
    if prev is None:
        return []
    changes: list[Change] = []
    prev_h, curr_h = set(prev.programs), set(curr.programs)

    for h in sorted(curr_h - prev_h):
        changes.append(_added_change(curr.programs[h], new_program_type))
    for h in sorted(prev_h - curr_h):
        p = prev.programs[h]
        changes.append(
            Change(
                frozenset({ChangeType.PROGRAM_REMOVED}), p.handle, p.name,
                p.submission_state, f"{p.name} is no longer accessible", {},
            )
        )
    for h in sorted(prev_h & curr_h):
        changes.extend(_diff_program(prev.programs[h], curr.programs[h]))
    return changes


def diff_api(prev: Snapshot | None, curr: Snapshot) -> list[Change]:
    return diff_snapshot(prev, curr, ChangeType.PROGRAM_ADDED)


def _diff_program(prev: Program, curr: Program) -> list[Change]:
    out: list[Change] = []
    if prev.offers_bounties != curr.offers_bounties:
        out.append(
            _mk(
                {ChangeType.BOUNTY_CHANGED}, curr,
                f"offers_bounties: {prev.offers_bounties} → {curr.offers_bounties}",
                {
                    "offers_bounties_from": prev.offers_bounties,
                    "offers_bounties_to": curr.offers_bounties,
                },
            )
        )
    if prev.submission_state != curr.submission_state:
        out.append(
            _mk(
                {ChangeType.PROGRAM_STATE}, curr,
                f"state: {prev.submission_state} → {curr.submission_state}",
                {
                    "submission_state_from": prev.submission_state,
                    "submission_state_to": curr.submission_state,
                    "became_paused": curr.submission_state == "paused",
                },
            )
        )
    if prev.policy != curr.policy:
        out.append(
            _mk(
                {ChangeType.PROGRAM_STATE}, curr, "policy text changed",
                {"policy_changed": True, "became_paused": False},
            )
        )
    prev_s, curr_s = set(prev.scopes), set(curr.scopes)
    for k in sorted(curr_s - prev_s):
        out.append(_mk({ChangeType.SCOPE_ADDED}, curr, f"scope added: {k}", {"scope_key": k}))
    for k in sorted(prev_s - curr_s):
        out.append(
            _mk({ChangeType.SCOPE_REMOVED}, curr, f"scope removed: {k}", {"scope_key": k})
        )
    for k in sorted(prev_s & curr_s):
        out.extend(_diff_scope(curr, k, prev.scopes[k], curr.scopes[k]))
    return out


def _diff_scope(prog: Program, key: str, a: Scope, b: Scope) -> list[Change]:
    types: set[ChangeType] = set()
    diffs: dict = {}
    for f in _SCOPE_FIELDS:
        if getattr(a, f) != getattr(b, f):
            types.add(ChangeType.SCOPE_MODIFIED)
            diffs[f] = [getattr(a, f), getattr(b, f)]
            if f == "max_severity":
                types.add(ChangeType.BOUNTY_CHANGED)
    if a.eligible_for_bounty != b.eligible_for_bounty:
        types.add(ChangeType.BOUNTY_CHANGED)
        diffs["eligible_for_bounty"] = [a.eligible_for_bounty, b.eligible_for_bounty]
    if not types:
        return []
    return [_mk(types, prog, f"scope modified: {key}", {"scope_key": key, "fields": diffs})]
