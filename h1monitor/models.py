from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum


class ChangeType(str, Enum):
    NEW_PUBLIC_PROGRAM = "new_public_program"
    SCOPE_ADDED = "scope_added"
    SCOPE_REMOVED = "scope_removed"
    SCOPE_MODIFIED = "scope_modified"
    BOUNTY_CHANGED = "bounty_changed"
    PROGRAM_ADDED = "program_added"
    PROGRAM_REMOVED = "program_removed"
    PROGRAM_STATE = "program_state"


class Category(str, Enum):
    NEW_PROGRAM = "🆕 New Program"
    SCOPE = "🎯 Scope Change"
    BOUNTY = "💰 Bounty"
    STATUS = "⏸ Program Status"
    NEW_ACCESS = "➕ New Access"
    REMOVED = "➖ Program Removed"


CATEGORY_FOR_TYPE: dict[ChangeType, Category] = {
    ChangeType.NEW_PUBLIC_PROGRAM: Category.NEW_PROGRAM,
    ChangeType.SCOPE_ADDED: Category.SCOPE,
    ChangeType.SCOPE_REMOVED: Category.SCOPE,
    ChangeType.SCOPE_MODIFIED: Category.SCOPE,
    ChangeType.BOUNTY_CHANGED: Category.BOUNTY,
    ChangeType.PROGRAM_ADDED: Category.NEW_ACCESS,
    ChangeType.PROGRAM_REMOVED: Category.REMOVED,
    ChangeType.PROGRAM_STATE: Category.STATUS,
}

_TYPE_ORDER = list(ChangeType)


@dataclass(frozen=True)
class Scope:
    asset_type: str
    asset_identifier: str
    eligible_for_bounty: bool
    eligible_for_submission: bool
    max_severity: str | None
    instruction: str | None
    confidentiality_requirement: str | None
    integrity_requirement: str | None
    availability_requirement: str | None
    updated_at: str | None
    reference: str | None = None

    @property
    def key(self) -> str:
        return f"{self.asset_type}:{self.asset_identifier}"


@dataclass
class Program:
    handle: str
    name: str
    submission_state: str | None
    offers_bounties: bool | None
    currency: str | None
    policy: str | None
    scopes: dict[str, Scope] = field(default_factory=dict)
    started_accepting_at: str | None = None


@dataclass(frozen=True)
class DirectoryProgram:
    handle: str
    name: str
    offers_bounties: bool
    submission_state: str | None
    started_accepting_at: str | None
    url: str | None


@dataclass
class Snapshot:
    programs: dict[str, Program] = field(default_factory=dict)


@dataclass
class Change:
    types: frozenset[ChangeType]
    program_handle: str
    program_name: str
    submission_state: str | None
    summary: str
    details: dict
    directory: DirectoryProgram | None = None

    @property
    def primary_type(self) -> ChangeType:
        return next(t for t in _TYPE_ORDER if t in self.types)

    @property
    def category(self) -> Category:
        return CATEGORY_FOR_TYPE[self.primary_type]


@dataclass
class Preferences:
    enabled: dict[ChangeType, bool]
    exclude_paused: bool = True
    poll_interval_minutes: int = 30
    private_interval_minutes: int = 120
    allowlist: frozenset[str] = frozenset()
    denylist: frozenset[str] = frozenset()

    @classmethod
    def defaults(cls) -> "Preferences":
        return cls(enabled={t: True for t in ChangeType})

    def is_type_enabled(self, t: ChangeType) -> bool:
        return self.enabled.get(t, True)

    def to_json(self) -> str:
        return json.dumps(
            {
                "enabled": {t.value: v for t, v in self.enabled.items()},
                "exclude_paused": self.exclude_paused,
                "poll_interval_minutes": self.poll_interval_minutes,
                "private_interval_minutes": self.private_interval_minutes,
                "allowlist": sorted(self.allowlist),
                "denylist": sorted(self.denylist),
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> "Preferences":
        d = json.loads(raw)
        enabled = {t: True for t in ChangeType}
        for k, v in d.get("enabled", {}).items():
            try:
                enabled[ChangeType(k)] = bool(v)
            except ValueError:
                continue
        return cls(
            enabled=enabled,
            exclude_paused=bool(d.get("exclude_paused", True)),
            poll_interval_minutes=int(d.get("poll_interval_minutes", 30)),
            private_interval_minutes=int(d.get("private_interval_minutes", 120)),
            allowlist=frozenset(d.get("allowlist", [])),
            denylist=frozenset(d.get("denylist", [])),
        )
