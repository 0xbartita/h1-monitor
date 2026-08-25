from __future__ import annotations

from h1monitor.models import Change, Preferences, ChangeType


def _passes_allow_deny(c: Change, prefs: Preferences) -> bool:
    if ChangeType.NEW_PUBLIC_PROGRAM in c.types:
        return True
    if c.program_handle in prefs.denylist:
        return False
    if prefs.allowlist and c.program_handle not in prefs.allowlist:
        return False
    return True


def _passes_paused(c: Change, prefs: Preferences) -> bool:
    """Drop the churn on a program nobody can report to, but never the pause
    itself — that alert is what explains the silence that follows."""
    if not prefs.exclude_paused:
        return True
    if c.submission_state != "paused":
        return True
    return ChangeType.PROGRAM_STATE in c.types and c.details.get("became_paused") is True


def _passes_type(c: Change, prefs: Preferences) -> bool:
    return any(prefs.is_type_enabled(t) for t in c.types)


def filter_changes(changes: list[Change], prefs: Preferences) -> list[Change]:
    return [
        c
        for c in changes
        if _passes_allow_deny(c, prefs)
        and _passes_paused(c, prefs)
        and _passes_type(c, prefs)
    ]
