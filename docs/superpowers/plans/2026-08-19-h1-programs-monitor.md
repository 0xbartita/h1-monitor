# h1-programs-monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-hosted Python daemon that polls HackerOne (REST Hacker API + public directory GraphQL), diffs program/scope/bounty state against snapshots, and pushes opted-in, category-labeled change alerts to a single Telegram chat with live `/config` toggles.

**Architecture:** One asyncio process runs three tasks — a REST poller (source 1: accessible programs' scope/bounty/state), a directory poller (source 2: platform-wide new public programs), and a Telegram long-polling bot for commands. Pure `differ`/`filters` functions turn snapshot pairs into `Change` objects; a `notifier` formats and sends them. State (snapshots, preferences, encrypted H1 creds) lives in SQLite.

**Tech Stack:** Python 3.11+, `python-telegram-bot` v21 (async), `httpx` (async HTTP), `cryptography` (Fernet), `pytest` + `pytest-asyncio`.

**Spec:** `docs/superpowers/specs/2026-08-19-h1-programs-monitor-design.md`

## Global Constraints

- Python **>= 3.11** (uses `X | Y` unions, `tomllib` not required).
- Dependencies limited to: `python-telegram-bot>=21`, `httpx>=0.27`, `cryptography>=42`; test-only: `pytest>=8`, `pytest-asyncio>=0.23`.
- Package name: `h1monitor`. Entry point: `python -m h1monitor`.
- **No secret ever printed to logs** (H1 creds, bot token, Fernet key).
- SQLite DB and Fernet keyfile created with mode **0600**.
- Only required env var is `TELEGRAM_BOT_TOKEN`; everything else optional (see spec §10).
- Change-type keys are exactly: `new_public_program`, `scope_added`, `scope_removed`, `scope_modified`, `bounty_changed`, `program_added`, `program_removed`, `program_state`. All default **on**.
- TDD rhythm for every task: write failing test → run & confirm it fails → minimal implementation → run & confirm pass → commit. Commit messages use Conventional Commits (`feat:`, `test:`, `chore:`).

---

### Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`, `h1monitor/__init__.py`, `tests/__init__.py`, `.env.example`, `tests/test_smoke.py`

**Interfaces:**
- Consumes: nothing.
- Produces: importable `h1monitor` package; `pytest` runnable.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "h1monitor"
version = "0.1.0"
description = "Self-hosted HackerOne program change monitor with Telegram alerts"
requires-python = ">=3.11"
dependencies = [
    "python-telegram-bot>=21",
    "httpx>=0.27",
    "cryptography>=42",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23"]

[project.scripts]
h1monitor = "h1monitor.main:run"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"
```

- [ ] **Step 2: Create package + test package init files** (`h1monitor/__init__.py` with `__version__ = "0.1.0"`, empty `tests/__init__.py`).

- [ ] **Step 3: Write `.env.example`**

```dotenv
# REQUIRED — the bot cannot receive its own token
TELEGRAM_BOT_TOKEN=

# Optional — auto-captured on first /start if omitted
TELEGRAM_OWNER_CHAT_ID=

# Optional — Fernet key for encrypting stored H1 creds. Auto-generated to
# ./h1mon_secret.key (0600) if omitted.
H1MON_SECRET_KEY=

# Optional — seed H1 creds instead of entering them via the bot
H1_API_USERNAME=
H1_API_TOKEN=

# Optional — session cookie for the directory GraphQL, if anonymous access fails
H1_DIRECTORY_COOKIE=

# Optional — default ./h1monitor.db
H1MON_DB_PATH=
```

- [ ] **Step 4: Write smoke test** `tests/test_smoke.py`

```python
def test_package_imports():
    import h1monitor
    assert h1monitor.__version__ == "0.1.0"
```

- [ ] **Step 5: Install dev deps + run** — `pip install -e '.[dev]'` then `pytest -q`. Expected: 1 passed.

- [ ] **Step 6: Commit** — `git add -A && git commit -m "chore: scaffold h1monitor package and tooling"`

---

### Task 2: Domain models (`models.py`)

**Files:**
- Create: `h1monitor/models.py`, `tests/test_models.py`

**Interfaces:**
- Produces:
  - `class ChangeType(str, Enum)` with the 8 keys from Global Constraints.
  - `class Category(str, Enum)`: `NEW_PROGRAM`, `SCOPE`, `BOUNTY`, `STATUS`, `NEW_ACCESS`, `REMOVED` (values are the label strings incl. emoji).
  - `CATEGORY_FOR_TYPE: dict[ChangeType, Category]`.
  - `@dataclass(frozen=True) Scope` with `.key -> str` property (`f"{asset_type}:{asset_identifier}"`).
  - `@dataclass Program(handle, name, submission_state, offers_bounties, currency, policy, scopes: dict[str, Scope])`.
  - `@dataclass(frozen=True) DirectoryProgram(handle, name, offers_bounties, submission_state, started_accepting_at, url)`.
  - `@dataclass Snapshot(programs: dict[str, Program])`.
  - `@dataclass Change(types: frozenset[ChangeType], program_handle, program_name, submission_state, summary: str, details: dict, directory: DirectoryProgram | None=None)` with `.primary_type -> ChangeType` (deterministic: first of `types` by ChangeType declaration order) and `.category -> Category`.
  - `@dataclass Preferences(enabled: dict[ChangeType, bool], exclude_paused: bool=True, poll_interval_minutes: int=30, allowlist: frozenset[str]=frozenset(), denylist: frozenset[str]=frozenset())` with `classmethod defaults()`, `is_type_enabled(t)->bool`, `to_json()->str`, `classmethod from_json(str)->Preferences`.

- [ ] **Step 1: Write failing tests** `tests/test_models.py`

```python
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
```

- [ ] **Step 2: Run — expect ImportError/FAIL.** `pytest tests/test_models.py -q`

- [ ] **Step 3: Implement `h1monitor/models.py`**

```python
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
    allowlist: frozenset[str] = frozenset()
    denylist: frozenset[str] = frozenset()

    @classmethod
    def defaults(cls) -> "Preferences":
        return cls(enabled={t: True for t in ChangeType})

    def is_type_enabled(self, t: ChangeType) -> bool:
        return self.enabled.get(t, True)

    def to_json(self) -> str:
        return json.dumps({
            "enabled": {t.value: v for t, v in self.enabled.items()},
            "exclude_paused": self.exclude_paused,
            "poll_interval_minutes": self.poll_interval_minutes,
            "allowlist": sorted(self.allowlist),
            "denylist": sorted(self.denylist),
        })

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
            allowlist=frozenset(d.get("allowlist", [])),
            denylist=frozenset(d.get("denylist", [])),
        )
```

- [ ] **Step 4: Run — expect PASS.** `pytest tests/test_models.py -q`
- [ ] **Step 5: Commit** — `git commit -am "feat: add domain models"`

---

### Task 3: Config & secrets (`config.py`)

**Files:**
- Create: `h1monitor/config.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `@dataclass Settings(telegram_bot_token, owner_chat_id: int|None, db_path: str, secret_key: bytes, seed_h1_username: str|None, seed_h1_token: str|None, directory_cookie: str|None)`.
  - `load_settings(env: Mapping[str,str] | None = None, base_dir: str = ".") -> Settings` — raises `ConfigError` if `TELEGRAM_BOT_TOKEN` missing. Resolves secret key: env `H1MON_SECRET_KEY` else read/create `<base_dir>/h1mon_secret.key` (0600).
  - `encrypt(secret_key: bytes, plaintext: str) -> str` and `decrypt(secret_key: bytes, token: str) -> str` (Fernet).
  - `class ConfigError(Exception)`.

- [ ] **Step 1: Write failing tests** `tests/test_config.py`

```python
import os, stat, pytest
from h1monitor.config import load_settings, encrypt, decrypt, ConfigError

def test_missing_bot_token_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_settings(env={}, base_dir=str(tmp_path))

def test_generates_keyfile_0600(tmp_path):
    s = load_settings(env={"TELEGRAM_BOT_TOKEN": "t"}, base_dir=str(tmp_path))
    kf = tmp_path / "h1mon_secret.key"
    assert kf.exists()
    assert stat.S_IMODE(kf.stat().st_mode) == 0o600
    assert s.telegram_bot_token == "t"
    assert s.owner_chat_id is None

def test_reads_owner_chat_id_int(tmp_path):
    s = load_settings(env={"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_OWNER_CHAT_ID": "42"},
                      base_dir=str(tmp_path))
    assert s.owner_chat_id == 42

def test_encrypt_decrypt_roundtrip(tmp_path):
    s = load_settings(env={"TELEGRAM_BOT_TOKEN": "t"}, base_dir=str(tmp_path))
    token = encrypt(s.secret_key, "hunter-secret")
    assert token != "hunter-secret"
    assert decrypt(s.secret_key, token) == "hunter-secret"

def test_reuses_existing_keyfile(tmp_path):
    s1 = load_settings(env={"TELEGRAM_BOT_TOKEN": "t"}, base_dir=str(tmp_path))
    s2 = load_settings(env={"TELEGRAM_BOT_TOKEN": "t"}, base_dir=str(tmp_path))
    assert s1.secret_key == s2.secret_key
```

- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement `h1monitor/config.py`**

```python
from __future__ import annotations
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from cryptography.fernet import Fernet


class ConfigError(Exception):
    pass


@dataclass
class Settings:
    telegram_bot_token: str
    owner_chat_id: int | None
    db_path: str
    secret_key: bytes
    seed_h1_username: str | None
    seed_h1_token: str | None
    directory_cookie: str | None


def _resolve_secret_key(env: Mapping[str, str], base_dir: str) -> bytes:
    raw = env.get("H1MON_SECRET_KEY")
    if raw:
        return raw.encode()
    keyfile = Path(base_dir) / "h1mon_secret.key"
    if keyfile.exists():
        return keyfile.read_bytes().strip()
    key = Fernet.generate_key()
    keyfile.write_bytes(key)
    os.chmod(keyfile, 0o600)
    return key


def load_settings(env: Mapping[str, str] | None = None, base_dir: str = ".") -> Settings:
    env = os.environ if env is None else env
    token = env.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ConfigError("TELEGRAM_BOT_TOKEN is required")
    chat_raw = env.get("TELEGRAM_OWNER_CHAT_ID")
    owner_chat_id = int(chat_raw) if chat_raw else None
    return Settings(
        telegram_bot_token=token,
        owner_chat_id=owner_chat_id,
        db_path=env.get("H1MON_DB_PATH", str(Path(base_dir) / "h1monitor.db")),
        secret_key=_resolve_secret_key(env, base_dir),
        seed_h1_username=env.get("H1_API_USERNAME") or None,
        seed_h1_token=env.get("H1_API_TOKEN") or None,
        directory_cookie=env.get("H1_DIRECTORY_COOKIE") or None,
    )


def encrypt(secret_key: bytes, plaintext: str) -> str:
    return Fernet(secret_key).encrypt(plaintext.encode()).decode()


def decrypt(secret_key: bytes, token: str) -> str:
    return Fernet(secret_key).decrypt(token.encode()).decode()
```

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** — `git commit -am "feat: add config loading and Fernet secret handling"`

---

### Task 4: Persistence store (`store.py`)

**Files:**
- Create: `h1monitor/store.py`, `tests/test_store.py`

**Interfaces:**
- Consumes: `models` (Snapshot, Program, Scope, Preferences), `config.encrypt/decrypt`.
- Produces `class Store`:
  - `__init__(self, path: str, secret_key: bytes)` — opens sqlite (WAL), creates schema, chmod 0600.
  - `get_preferences() -> Preferences` / `save_preferences(p: Preferences) -> None`.
  - `set_h1_credentials(username: str, token: str) -> None` (encrypts) / `get_h1_credentials() -> tuple[str, str] | None`.
  - `get_owner_chat_id() -> int | None` / `set_owner_chat_id(chat_id: int) -> None`.
  - `load_api_snapshot() -> Snapshot | None` / `save_api_snapshot(s: Snapshot) -> None`.
  - `has_directory_baseline() -> bool` / `load_directory_handles() -> set[str]` / `save_directory_handles(handles: set[str]) -> None`.
  - `record_poll(source: str, ts: float) -> None` / `get_last_poll(source: str) -> float | None`.
  - `mark_alert_sent(key: str) -> bool` — returns True the first time a key is seen (for once-only alerts), False after.
  - `clear_alert(key: str) -> None` — resets an alert key so it can fire again.
  - `close() -> None`.
- Serialization: snapshots stored as JSON blobs in a `kv(key TEXT PRIMARY KEY, value TEXT)` table; credentials in a `credentials` table. All writes guarded by a `threading.Lock`.

- [ ] **Step 1: Write failing tests** `tests/test_store.py`

```python
import stat, pytest
from cryptography.fernet import Fernet
from h1monitor.store import Store
from h1monitor.models import Snapshot, Program, Scope, Preferences, ChangeType

@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "t.db"), Fernet.generate_key())
    yield s
    s.close()

def test_db_file_is_0600(tmp_path):
    p = tmp_path / "t.db"
    Store(str(p), Fernet.generate_key()).close()
    assert stat.S_IMODE(p.stat().st_mode) == 0o600

def test_preferences_roundtrip(store):
    p = Preferences.defaults()
    p.enabled[ChangeType.BOUNTY_CHANGED] = False
    store.save_preferences(p)
    assert store.get_preferences().is_type_enabled(ChangeType.BOUNTY_CHANGED) is False

def test_default_preferences_when_empty(store):
    assert store.get_preferences().is_type_enabled(ChangeType.SCOPE_ADDED) is True

def test_credentials_encrypted_and_roundtrip(store, tmp_path):
    store.set_h1_credentials("id123", "tok456")
    assert store.get_h1_credentials() == ("id123", "tok456")
    # raw DB value is not plaintext
    import sqlite3
    row = sqlite3.connect(str(tmp_path / "t.db")).execute(
        "select value from credentials where name='h1_token'").fetchone()
    assert "tok456" not in (row[0] if row else "")

def test_owner_chat_id_roundtrip(store):
    assert store.get_owner_chat_id() is None
    store.set_owner_chat_id(99)
    assert store.get_owner_chat_id() == 99

def test_api_snapshot_roundtrip(store):
    prog = Program("acme", "Acme", "open", True, "USD", "policy",
                   {"URL:a.com": Scope("URL", "a.com", True, True, "high",
                                       None, None, None, None, "2026-01-01")})
    store.save_api_snapshot(Snapshot({"acme": prog}))
    loaded = store.load_api_snapshot()
    assert loaded.programs["acme"].scopes["URL:a.com"].max_severity == "high"

def test_directory_baseline_and_handles(store):
    assert store.has_directory_baseline() is False
    store.save_directory_handles({"a", "b"})
    assert store.has_directory_baseline() is True
    assert store.load_directory_handles() == {"a", "b"}

def test_alert_once(store):
    assert store.mark_alert_sent("h1-down") is True
    assert store.mark_alert_sent("h1-down") is False
    store.clear_alert("h1-down")
    assert store.mark_alert_sent("h1-down") is True
```

- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement `h1monitor/store.py`** (JSON (de)serialization of Snapshot via helper functions; WAL; lock).

```python
from __future__ import annotations
import json, os, sqlite3, threading
from h1monitor.models import Snapshot, Program, Scope, Preferences
from h1monitor.config import encrypt, decrypt


def _scope_to_dict(s: Scope) -> dict:
    return {
        "asset_type": s.asset_type, "asset_identifier": s.asset_identifier,
        "eligible_for_bounty": s.eligible_for_bounty,
        "eligible_for_submission": s.eligible_for_submission,
        "max_severity": s.max_severity, "instruction": s.instruction,
        "confidentiality_requirement": s.confidentiality_requirement,
        "integrity_requirement": s.integrity_requirement,
        "availability_requirement": s.availability_requirement,
        "updated_at": s.updated_at, "reference": s.reference,
    }


def _scope_from_dict(d: dict) -> Scope:
    return Scope(
        d["asset_type"], d["asset_identifier"], d["eligible_for_bounty"],
        d["eligible_for_submission"], d.get("max_severity"), d.get("instruction"),
        d.get("confidentiality_requirement"), d.get("integrity_requirement"),
        d.get("availability_requirement"), d.get("updated_at"), d.get("reference"),
    )


def _snapshot_to_json(s: Snapshot) -> str:
    return json.dumps({
        h: {
            "handle": p.handle, "name": p.name, "submission_state": p.submission_state,
            "offers_bounties": p.offers_bounties, "currency": p.currency,
            "policy": p.policy,
            "scopes": {k: _scope_to_dict(v) for k, v in p.scopes.items()},
        } for h, p in s.programs.items()
    })


def _snapshot_from_json(raw: str) -> Snapshot:
    d = json.loads(raw)
    progs = {}
    for h, pd in d.items():
        progs[h] = Program(
            pd["handle"], pd["name"], pd.get("submission_state"),
            pd.get("offers_bounties"), pd.get("currency"), pd.get("policy"),
            {k: _scope_from_dict(v) for k, v in pd.get("scopes", {}).items()},
        )
    return Snapshot(progs)


class Store:
    def __init__(self, path: str, secret_key: bytes):
        self._key = secret_key
        self._lock = threading.Lock()
        newfile = not os.path.exists(path)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(
            "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);"
            "CREATE TABLE IF NOT EXISTS credentials (name TEXT PRIMARY KEY, value TEXT);"
            "CREATE TABLE IF NOT EXISTS alerts (key TEXT PRIMARY KEY);"
        )
        self._db.commit()
        if newfile:
            os.chmod(path, 0o600)

    # --- kv helpers ---
    def _get(self, key: str) -> str | None:
        row = self._db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def _set(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO kv(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
            self._db.commit()

    # --- preferences ---
    def get_preferences(self) -> Preferences:
        raw = self._get("preferences")
        return Preferences.from_json(raw) if raw else Preferences.defaults()

    def save_preferences(self, p: Preferences) -> None:
        self._set("preferences", p.to_json())

    # --- credentials ---
    def set_h1_credentials(self, username: str, token: str) -> None:
        with self._lock:
            for name, val in (("h1_username", username), ("h1_token", token)):
                self._db.execute(
                    "INSERT INTO credentials(name,value) VALUES(?,?) "
                    "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                    (name, encrypt(self._key, val)))
            self._db.commit()

    def get_h1_credentials(self) -> tuple[str, str] | None:
        rows = dict(self._db.execute(
            "SELECT name,value FROM credentials").fetchall())
        if "h1_username" in rows and "h1_token" in rows:
            return (decrypt(self._key, rows["h1_username"]),
                    decrypt(self._key, rows["h1_token"]))
        return None

    # --- owner ---
    def get_owner_chat_id(self) -> int | None:
        raw = self._get("owner_chat_id")
        return int(raw) if raw else None

    def set_owner_chat_id(self, chat_id: int) -> None:
        self._set("owner_chat_id", str(chat_id))

    # --- api snapshot ---
    def load_api_snapshot(self) -> Snapshot | None:
        raw = self._get("api_snapshot")
        return _snapshot_from_json(raw) if raw else None

    def save_api_snapshot(self, s: Snapshot) -> None:
        self._set("api_snapshot", _snapshot_to_json(s))

    # --- directory ---
    def has_directory_baseline(self) -> bool:
        return self._get("directory_handles") is not None

    def load_directory_handles(self) -> set[str]:
        raw = self._get("directory_handles")
        return set(json.loads(raw)) if raw else set()

    def save_directory_handles(self, handles: set[str]) -> None:
        self._set("directory_handles", json.dumps(sorted(handles)))

    # --- poll metadata ---
    def record_poll(self, source: str, ts: float) -> None:
        self._set(f"last_poll:{source}", str(ts))

    def get_last_poll(self, source: str) -> float | None:
        raw = self._get(f"last_poll:{source}")
        return float(raw) if raw else None

    # --- alert dedup ---
    def mark_alert_sent(self, key: str) -> bool:
        with self._lock:
            cur = self._db.execute(
                "INSERT OR IGNORE INTO alerts(key) VALUES(?)", (key,))
            self._db.commit()
            return cur.rowcount == 1

    def clear_alert(self, key: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM alerts WHERE key=?", (key,))
            self._db.commit()

    def close(self) -> None:
        self._db.close()
```

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** — `git commit -am "feat: add SQLite store with encrypted credentials"`

---

### Task 5: Differ — API source (`differ.py::diff_api`)

**Files:**
- Create: `h1monitor/differ.py`, `tests/test_differ_api.py`

**Interfaces:**
- Consumes: `models`.
- Produces: `diff_api(prev: Snapshot | None, curr: Snapshot) -> list[Change]`. First run (`prev is None`) → `[]` (baseline). Emits, per program: `program_added` (handle new), `program_removed` (handle gone), and for surviving programs: `scope_added`, `scope_removed`, `scope_modified`, `bounty_changed`, `program_state`. Each `Change.submission_state` = the program's **current** state (for removed programs, the previous state). `program_state` changes set `details["became_paused"] = (new_state == "paused")`.

- [ ] **Step 1: Write failing tests** `tests/test_differ_api.py`

```python
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

def test_policy_change_is_program_state():
    prev = Snapshot({"acme": _prog(policy="old")})
    curr = Snapshot({"acme": _prog(policy="new")})
    assert any(ChangeType.PROGRAM_STATE in c.types for c in diff_api(prev, curr))
```

- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement `diff_api` in `h1monitor/differ.py`**

```python
from __future__ import annotations
from h1monitor.models import Snapshot, Program, Scope, Change, ChangeType

_SCOPE_FIELDS = [
    "max_severity", "instruction", "eligible_for_submission",
    "confidentiality_requirement", "integrity_requirement",
    "availability_requirement",
]


def _mk(types, prog: Program, summary: str, details: dict) -> Change:
    return Change(frozenset(types), prog.handle, prog.name,
                  prog.submission_state, summary, details)


def diff_api(prev: Snapshot | None, curr: Snapshot) -> list[Change]:
    if prev is None:
        return []
    changes: list[Change] = []
    prev_h, curr_h = set(prev.programs), set(curr.programs)

    for h in sorted(curr_h - prev_h):
        p = curr.programs[h]
        changes.append(_mk({ChangeType.PROGRAM_ADDED}, p,
                           f"{p.name} is now accessible to you", {}))
    for h in sorted(prev_h - curr_h):
        p = prev.programs[h]
        changes.append(Change(frozenset({ChangeType.PROGRAM_REMOVED}), p.handle,
                              p.name, p.submission_state,
                              f"{p.name} is no longer accessible", {}))

    for h in sorted(prev_h & curr_h):
        changes.extend(_diff_program(prev.programs[h], curr.programs[h]))
    return changes


def _diff_program(prev: Program, curr: Program) -> list[Change]:
    out: list[Change] = []
    # program-level bounty toggle
    if prev.offers_bounties != curr.offers_bounties:
        out.append(_mk({ChangeType.BOUNTY_CHANGED}, curr,
                       f"offers_bounties: {prev.offers_bounties} → {curr.offers_bounties}",
                       {"offers_bounties_from": prev.offers_bounties,
                        "offers_bounties_to": curr.offers_bounties}))
    # state / policy
    if prev.submission_state != curr.submission_state:
        out.append(_mk({ChangeType.PROGRAM_STATE}, curr,
                       f"state: {prev.submission_state} → {curr.submission_state}",
                       {"submission_state_from": prev.submission_state,
                        "submission_state_to": curr.submission_state,
                        "became_paused": curr.submission_state == "paused"}))
    if prev.policy != curr.policy:
        out.append(_mk({ChangeType.PROGRAM_STATE}, curr, "policy text changed",
                       {"policy_changed": True, "became_paused": False}))
    # scopes
    prev_s, curr_s = set(prev.scopes), set(curr.scopes)
    for k in sorted(curr_s - prev_s):
        out.append(_mk({ChangeType.SCOPE_ADDED}, curr,
                       f"scope added: {k}", {"scope_key": k}))
    for k in sorted(prev_s - curr_s):
        out.append(_mk({ChangeType.SCOPE_REMOVED}, curr,
                       f"scope removed: {k}", {"scope_key": k}))
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
    return [_mk(types, prog, f"scope modified: {key}",
               {"scope_key": key, "fields": diffs})]
```

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** — `git commit -am "feat: add API snapshot differ"`

---

### Task 6: Differ — directory source (`diff_directory`)

**Files:**
- Modify: `h1monitor/differ.py`
- Create: `tests/test_differ_directory.py`

**Interfaces:**
- Produces: `diff_directory(prev_handles: set[str], curr: list[DirectoryProgram], first_run: bool) -> list[Change]`. `first_run` → `[]` (silent baseline). Otherwise one `Change` (type `new_public_program`, category New Program) per program whose handle is not in `prev_handles`; `Change.directory` holds the `DirectoryProgram`; `Change.submission_state` = program's state.

- [ ] **Step 1: Write failing tests** `tests/test_differ_directory.py`

```python
from h1monitor.differ import diff_directory
from h1monitor.models import DirectoryProgram, ChangeType

def _dp(handle, state="open"):
    return DirectoryProgram(handle, handle.title(), True, state,
                            "2026-08-18", f"https://hackerone.com/{handle}")

def test_first_run_silent():
    assert diff_directory(set(), [_dp("a"), _dp("b")], first_run=True) == []

def test_new_program_detected():
    changes = diff_directory({"a"}, [_dp("a"), _dp("vercel")], first_run=False)
    assert len(changes) == 1
    c = changes[0]
    assert ChangeType.NEW_PUBLIC_PROGRAM in c.types
    assert c.program_handle == "vercel"
    assert c.directory.started_accepting_at == "2026-08-18"

def test_no_new_programs():
    assert diff_directory({"a", "b"}, [_dp("a"), _dp("b")], first_run=False) == []
```

- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Append `diff_directory` to `h1monitor/differ.py`**

```python
from h1monitor.models import DirectoryProgram  # add to existing imports


def diff_directory(prev_handles: set[str], curr: list[DirectoryProgram],
                   first_run: bool) -> list[Change]:
    if first_run:
        return []
    out: list[Change] = []
    for p in curr:
        if p.handle not in prev_handles:
            out.append(Change(
                frozenset({ChangeType.NEW_PUBLIC_PROGRAM}), p.handle, p.name,
                p.submission_state, f"New public program: {p.name}",
                {"started_accepting_at": p.started_accepting_at}, directory=p))
    return out
```

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** — `git commit -am "feat: add directory differ for new public programs"`

---

### Task 7: Preference filtering (`filters.py`)

**Files:**
- Create: `h1monitor/filters.py`, `tests/test_filters.py`

**Interfaces:**
- Consumes: `models` (Change, Preferences, ChangeType).
- Produces: `filter_changes(changes: list[Change], prefs: Preferences) -> list[Change]`. Order: (1) allow/deny by `program_handle` — **skipped for** `new_public_program` (directory-wide); (2) `exclude_paused`: drop changes whose `submission_state == "paused"`, **except** a `program_state` change with `details["became_paused"] is True`; (3) type toggle: keep if `any(prefs.is_type_enabled(t) for t in change.types)`.

- [ ] **Step 1: Write failing tests** `tests/test_filters.py`

```python
from h1monitor.filters import filter_changes
from h1monitor.models import Change, Preferences, ChangeType

def _c(types, handle="acme", state="open", details=None):
    return Change(frozenset(types), handle, handle.title(), state, "s", details or {})

def test_type_toggle_off_drops():
    p = Preferences.defaults(); p.enabled[ChangeType.SCOPE_ADDED] = False
    assert filter_changes([_c({ChangeType.SCOPE_ADDED})], p) == []

def test_dual_tag_kept_if_any_enabled():
    p = Preferences.defaults(); p.enabled[ChangeType.SCOPE_MODIFIED] = False
    kept = filter_changes([_c({ChangeType.SCOPE_MODIFIED, ChangeType.BOUNTY_CHANGED})], p)
    assert len(kept) == 1  # BOUNTY_CHANGED still on

def test_denylist_drops_non_directory():
    p = Preferences.defaults(); p.denylist = frozenset({"acme"})
    assert filter_changes([_c({ChangeType.SCOPE_ADDED}, handle="acme")], p) == []

def test_allowlist_only():
    p = Preferences.defaults(); p.allowlist = frozenset({"beta"})
    got = filter_changes([_c({ChangeType.SCOPE_ADDED}, handle="acme"),
                          _c({ChangeType.SCOPE_ADDED}, handle="beta")], p)
    assert [c.program_handle for c in got] == ["beta"]

def test_new_public_program_ignores_allow_deny():
    p = Preferences.defaults(); p.denylist = frozenset({"vercel"})
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
    p = Preferences.defaults(); p.exclude_paused = False
    assert len(filter_changes([_c({ChangeType.SCOPE_ADDED}, state="paused")], p)) == 1
```

- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement `h1monitor/filters.py`**

```python
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
    if not prefs.exclude_paused:
        return True
    if c.submission_state != "paused":
        return True
    return ChangeType.PROGRAM_STATE in c.types and c.details.get("became_paused") is True


def _passes_type(c: Change, prefs: Preferences) -> bool:
    return any(prefs.is_type_enabled(t) for t in c.types)


def filter_changes(changes: list[Change], prefs: Preferences) -> list[Change]:
    return [c for c in changes
            if _passes_allow_deny(c, prefs)
            and _passes_paused(c, prefs)
            and _passes_type(c, prefs)]
```

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** — `git commit -am "feat: add preference-based change filtering"`

---

### Task 8: REST Hacker API client (`h1_client.py`)

**Files:**
- Create: `h1monitor/h1_client.py`, `tests/test_h1_client.py`

**Interfaces:**
- Consumes: `models` (Snapshot, Program, Scope), `httpx`.
- Produces `class H1Client`:
  - `__init__(self, username: str, token: str, base_url="https://api.hackerone.com/v1", transport=None)` — builds `httpx.AsyncClient(auth=(username, token), base_url=..., transport=transport)`.
  - `async fetch_snapshot(self, previous: Snapshot | None = None) -> Snapshot` — pages `/hackers/programs`; per handle pages `/hackers/programs/{handle}/structured_scopes`; on per-program HTTP error, reuse `previous.programs[handle]` if present else skip. Program-level fields (`submission_state`, `offers_bounties`, `currency`, `policy`, `name`) come from the program list item's `attributes`.
  - `async aclose(self) -> None`.
- Helpers (module-level, unit-tested): `_parse_program_item(item: dict) -> Program` and `_parse_scope_item(item: dict) -> Scope` — read JSON:API `{"id":..,"attributes":{..}}` shapes.
- Pagination: follow `body["links"]["next"]` (absolute URL) until absent. Retry `429` honoring `Retry-After`, and `>=500` with capped exponential backoff (max 3 tries).

- [ ] **Step 1: Write failing tests** `tests/test_h1_client.py` (use `httpx.MockTransport`)

```python
import httpx, pytest
from h1monitor.h1_client import H1Client, _parse_program_item, _parse_scope_item

def test_parse_program_item():
    item = {"id": "1", "attributes": {"handle": "acme", "name": "Acme",
            "submission_state": "open", "offers_bounties": True,
            "currency": "USD", "policy": "p"}}
    p = _parse_program_item(item)
    assert (p.handle, p.name, p.offers_bounties) == ("acme", "Acme", True)

def test_parse_scope_item():
    item = {"id": "9", "attributes": {"asset_type": "URL",
            "asset_identifier": "a.com", "eligible_for_bounty": True,
            "eligible_for_submission": True, "max_severity": "high",
            "instruction": "x", "updated_at": "t"}}
    s = _parse_scope_item(item)
    assert s.key == "URL:a.com" and s.max_severity == "high"

@pytest.mark.asyncio
async def test_fetch_snapshot_paginates_programs_and_scopes():
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/hackers/programs"):
            return httpx.Response(200, json={"data": [
                {"id": "1", "attributes": {"handle": "acme", "name": "Acme",
                 "submission_state": "open", "offers_bounties": True,
                 "currency": "USD", "policy": "p"}}], "links": {}})
        if "structured_scopes" in url:
            return httpx.Response(200, json={"data": [
                {"id": "9", "attributes": {"asset_type": "URL",
                 "asset_identifier": "a.com", "eligible_for_bounty": True,
                 "eligible_for_submission": True, "max_severity": "high",
                 "updated_at": "t"}}], "links": {}})
        return httpx.Response(404)
    client = H1Client("id", "tok", transport=httpx.MockTransport(handler))
    snap = await client.fetch_snapshot()
    await client.aclose()
    assert "acme" in snap.programs
    assert snap.programs["acme"].scopes["URL:a.com"].max_severity == "high"

@pytest.mark.asyncio
async def test_per_program_error_reuses_previous():
    from h1monitor.models import Snapshot, Program, Scope
    prev = Snapshot({"acme": Program("acme", "Acme", "open", True, "USD", "p",
        {"URL:a.com": Scope("URL", "a.com", True, True, "low",
                            None, None, None, None, "t")})})
    def handler(request):
        url = str(request.url)
        if url.endswith("/hackers/programs"):
            return httpx.Response(200, json={"data": [
                {"id": "1", "attributes": {"handle": "acme", "name": "Acme",
                 "submission_state": "open", "offers_bounties": True,
                 "currency": "USD", "policy": "p"}}], "links": {}})
        return httpx.Response(500)  # scopes fail
    client = H1Client("id", "tok", transport=httpx.MockTransport(handler))
    snap = await client.fetch_snapshot(previous=prev)
    await client.aclose()
    assert snap.programs["acme"].scopes["URL:a.com"].max_severity == "low"
```

- [ ] **Step 3: Implement `h1monitor/h1_client.py`** (async, `MockTransport`-friendly; backoff without real sleeps in tests by making retries only on the shared `_request` and using `asyncio.sleep`; tests above never hit the retry path more than the terminal 500 which is caught per-program).

```python
from __future__ import annotations
import asyncio
import httpx
from h1monitor.models import Snapshot, Program, Scope

_MAX_TRIES = 3


def _parse_program_item(item: dict) -> Program:
    a = item.get("attributes", {})
    return Program(a.get("handle"), a.get("name"), a.get("submission_state"),
                   a.get("offers_bounties"), a.get("currency"), a.get("policy"), {})


def _parse_scope_item(item: dict) -> Scope:
    a = item.get("attributes", {})
    return Scope(
        a.get("asset_type"), a.get("asset_identifier"),
        bool(a.get("eligible_for_bounty")), bool(a.get("eligible_for_submission")),
        a.get("max_severity"), a.get("instruction"),
        a.get("confidentiality_requirement"), a.get("integrity_requirement"),
        a.get("availability_requirement"), a.get("updated_at"), a.get("reference"))


class H1Client:
    def __init__(self, username: str, token: str,
                 base_url: str = "https://api.hackerone.com/v1", transport=None):
        self._client = httpx.AsyncClient(
            auth=(username, token), base_url=base_url, transport=transport,
            timeout=30.0, headers={"Accept": "application/json"})

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, url: str) -> dict:
        for attempt in range(_MAX_TRIES):
            resp = await self._client.get(url)
            if resp.status_code == 429 and attempt < _MAX_TRIES - 1:
                await asyncio.sleep(float(resp.headers.get("Retry-After", "1")))
                continue
            if resp.status_code >= 500 and attempt < _MAX_TRIES - 1:
                await asyncio.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()
        return resp.json()

    async def _paginate(self, first_url: str) -> list[dict]:
        items: list[dict] = []
        url: str | None = first_url
        while url:
            body = await self._get(url)
            items.extend(body.get("data", []))
            url = (body.get("links") or {}).get("next")
        return items

    async def fetch_snapshot(self, previous: Snapshot | None = None) -> Snapshot:
        programs: dict[str, Program] = {}
        for item in await self._paginate("/hackers/programs"):
            prog = _parse_program_item(item)
            if not prog.handle:
                continue
            try:
                scope_items = await self._paginate(
                    f"/hackers/programs/{prog.handle}/structured_scopes")
                prog.scopes = {s.key: s for s in map(_parse_scope_item, scope_items)}
            except httpx.HTTPError:
                if previous and prog.handle in previous.programs:
                    prog.scopes = previous.programs[prog.handle].scopes
                else:
                    continue
            programs[prog.handle] = prog
        return Snapshot(programs)
```

- [ ] **Step 4: Run — expect PASS.** `pytest tests/test_h1_client.py -q`
- [ ] **Step 5: Commit** — `git commit -am "feat: add HackerOne REST API client"`

---

### Task 9: Directory GraphQL client (`directory_client.py`)

**Files:**
- Create: `h1monitor/directory_client.py`, `tests/test_directory_client.py`

**Interfaces:**
- Consumes: `models` (DirectoryProgram), `httpx`.
- Produces `class DirectoryClient`:
  - `__init__(self, cookie: str | None = None, base="https://hackerone.com", transport=None)`.
  - `async fetch_all(self) -> list[DirectoryProgram]` — POSTs the `DirectoryQuery` to `/graphql`, paginating on `data.teams.pageInfo.{hasNextPage,endCursor}`, mapping edges→`DirectoryProgram`.
  - `async aclose(self)`.
- Helper: `_parse_team_node(node: dict) -> DirectoryProgram`.
- **Defensive parsing:** missing fields default to `None`/`False`; a node without `handle` is skipped.
- The exact GraphQL query string is defined as module constant `DIRECTORY_QUERY`. (Spec §14 spike may refine it; parsing is tested against the documented shape.)

- [ ] **Step 1: Write failing tests** `tests/test_directory_client.py`

```python
import httpx, pytest
from h1monitor.directory_client import DirectoryClient, _parse_team_node

def test_parse_team_node_defensive():
    node = {"handle": "vercel", "name": "Vercel", "offers_bounties": True,
            "submission_state": "open", "started_accepting_at": "2026-08-18",
            "url": "https://hackerone.com/vercel"}
    dp = _parse_team_node(node)
    assert dp.handle == "vercel" and dp.started_accepting_at == "2026-08-18"

def test_parse_team_node_missing_fields():
    dp = _parse_team_node({"handle": "x"})
    assert dp.handle == "x" and dp.offers_bounties is False and dp.url is None

@pytest.mark.asyncio
async def test_fetch_all_paginates():
    pages = [
        {"data": {"teams": {"pageInfo": {"hasNextPage": True, "endCursor": "c1"},
            "edges": [{"node": {"handle": "a", "name": "A", "offers_bounties": True,
                       "submission_state": "open", "started_accepting_at": "d",
                       "url": "u"}}]}}},
        {"data": {"teams": {"pageInfo": {"hasNextPage": False, "endCursor": None},
            "edges": [{"node": {"handle": "b", "name": "B", "offers_bounties": False,
                       "submission_state": "open", "started_accepting_at": "d",
                       "url": "u"}}]}}},
    ]
    calls = {"n": 0}
    def handler(request):
        if request.url.path == "/directory/programs":
            return httpx.Response(200, text="ok",
                                  headers={"set-cookie": "s=1", "x-csrf-token": "tok"})
        i = calls["n"]; calls["n"] += 1
        return httpx.Response(200, json=pages[i])
    c = DirectoryClient(transport=httpx.MockTransport(handler))
    progs = await c.fetch_all()
    await c.aclose()
    assert [p.handle for p in progs] == ["a", "b"]
```

- [ ] **Step 3: Implement `h1monitor/directory_client.py`**

```python
from __future__ import annotations
import httpx
from h1monitor.models import DirectoryProgram

DIRECTORY_QUERY = """
query DirectoryQuery($cursor: String) {
  teams(first: 50, after: $cursor,
        secure_order_by: {started_accepting_at: {_direction: DESC}},
        where: {_and: [{submission_state: {_eq: open}},
                       {_not: {external_program: {_is_null: false}}}]}) {
    pageInfo { hasNextPage endCursor }
    edges { node { handle name offers_bounties submission_state
                   started_accepting_at url } }
  }
}
""".strip()


def _parse_team_node(node: dict) -> DirectoryProgram:
    return DirectoryProgram(
        handle=node.get("handle"),
        name=node.get("name") or node.get("handle") or "",
        offers_bounties=bool(node.get("offers_bounties")),
        submission_state=node.get("submission_state"),
        started_accepting_at=node.get("started_accepting_at"),
        url=node.get("url"),
    )


class DirectoryClient:
    def __init__(self, cookie: str | None = None,
                 base: str = "https://hackerone.com", transport=None):
        self._client = httpx.AsyncClient(base_url=base, transport=transport,
                                         timeout=30.0)
        self._cookie = cookie
        self._csrf: str | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _bootstrap(self) -> None:
        if self._cookie and self._csrf:
            return
        r = await self._client.get("/directory/programs")
        if self._cookie is None:
            self._cookie = r.headers.get("set-cookie", "")
        self._csrf = r.headers.get("x-csrf-token", "")

    async def fetch_all(self) -> list[DirectoryProgram]:
        await self._bootstrap()
        headers = {"Content-Type": "application/json"}
        if self._cookie:
            headers["Cookie"] = self._cookie
        if self._csrf:
            headers["X-Csrf-Token"] = self._csrf
        out: list[DirectoryProgram] = []
        cursor: str | None = None
        while True:
            resp = await self._client.post("/graphql", headers=headers, json={
                "query": DIRECTORY_QUERY, "variables": {"cursor": cursor}})
            resp.raise_for_status()
            teams = (resp.json().get("data") or {}).get("teams") or {}
            for edge in teams.get("edges", []):
                node = edge.get("node") or {}
                if node.get("handle"):
                    out.append(_parse_team_node(node))
            page = teams.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                break
            cursor = page.get("endCursor")
            if not cursor:
                break
        return out
```

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** — `git commit -am "feat: add HackerOne directory GraphQL client"`

---

### Task 10: Notifier (`notifier.py`)

**Files:**
- Create: `h1monitor/notifier.py`, `tests/test_notifier.py`

**Interfaces:**
- Consumes: `models` (Change, Category, ChangeType, DirectoryProgram).
- Produces:
  - `format_change(c: Change) -> str` — HTML string beginning with the category label. For `new_public_program` uses the reference format: `🆕 New Program: {name}` / body `"{name} launched on {date} as a {kind}."` (or `"...was newly observed as a {kind}."` when no date), kind = `Bug bounty program` if `offers_bounties` else `vulnerability disclosure program`; plus `Open program → https://hackerone.com/{handle}`. Other categories: `"{label}\n<b>{program_name}</b> ({handle})\n{summary}"`.
  - `class Notifier(bot, chat_id)` with `async send_changes(changes: list[Change]) -> None` (groups by `program_handle`, one message per group, splits at ~3800 chars) and `async send_text(text: str) -> None`. Uses `bot.send_message(chat_id, text, parse_mode="HTML", disable_web_page_preview=True)`.
- `escape_html(s)` used on all interpolated program/summary text.

- [ ] **Step 1: Write failing tests** `tests/test_notifier.py`

```python
import pytest
from h1monitor.notifier import format_change, Notifier
from h1monitor.models import Change, ChangeType, DirectoryProgram

def _dir_change(date="2026-08-18", bounties=True):
    dp = DirectoryProgram("vercel", "Vercel Sandbox", bounties, "open", date,
                          "https://hackerone.com/vercel")
    return Change(frozenset({ChangeType.NEW_PUBLIC_PROGRAM}), "vercel",
                  "Vercel Sandbox", "open", "New public program", {}, directory=dp)

def test_new_program_format_with_date():
    text = format_change(_dir_change())
    assert "🆕 New Program: Vercel Sandbox" in text
    assert "launched on 2026-08-18 as a Bug bounty program" in text
    assert "hackerone.com/vercel" in text

def test_new_program_format_without_date():
    text = format_change(_dir_change(date=None))
    assert "was newly observed as a Bug bounty program" in text

def test_vdp_wording():
    assert "vulnerability disclosure program" in format_change(_dir_change(bounties=False))

def test_scope_change_format_has_category_and_handle():
    c = Change(frozenset({ChangeType.SCOPE_ADDED}), "acme", "Acme", "open",
               "scope added: URL:a.com", {"scope_key": "URL:a.com"})
    text = format_change(c)
    assert "🎯 Scope Change" in text and "acme" in text and "URL:a.com" in text

@pytest.mark.asyncio
async def test_notifier_groups_and_sends():
    sent = []
    class FakeBot:
        async def send_message(self, chat_id, text, **kw): sent.append((chat_id, text))
    n = Notifier(FakeBot(), 42)
    await n.send_changes([
        Change(frozenset({ChangeType.SCOPE_ADDED}), "acme", "Acme", "open", "a", {}),
        Change(frozenset({ChangeType.SCOPE_REMOVED}), "acme", "Acme", "open", "b", {}),
        Change(frozenset({ChangeType.SCOPE_ADDED}), "beta", "Beta", "open", "c", {}),
    ])
    assert len(sent) == 2  # grouped by handle: acme, beta
    assert all(m[0] == 42 for m in sent)
```

- [ ] **Step 3: Implement `h1monitor/notifier.py`**

```python
from __future__ import annotations
from html import escape
from h1monitor.models import Change, ChangeType

_MAX = 3800


def escape_html(s: str) -> str:
    return escape(s or "")


def _format_new_program(c: Change) -> str:
    dp = c.directory
    kind = "Bug bounty program" if (dp and dp.offers_bounties) \
        else "vulnerability disclosure program"
    name = escape_html(dp.name if dp else c.program_name)
    if dp and dp.started_accepting_at:
        body = f"<b>{name}</b> launched on {escape_html(dp.started_accepting_at)} as a {kind}."
    else:
        body = f"<b>{name}</b> was newly observed as a {kind}."
    url = (dp.url if dp and dp.url else f"https://hackerone.com/{c.program_handle}")
    return (f"🆕 New Program: {name}\n{body}\n"
            f'<a href="{escape_html(url)}">Open program</a>')


def format_change(c: Change) -> str:
    if ChangeType.NEW_PUBLIC_PROGRAM in c.types:
        return _format_new_program(c)
    label = c.category.value
    return (f"{label}\n<b>{escape_html(c.program_name)}</b> "
            f"({escape_html(c.program_handle)})\n{escape_html(c.summary)}")


class Notifier:
    def __init__(self, bot, chat_id: int):
        self._bot = bot
        self._chat_id = chat_id

    async def send_text(self, text: str) -> None:
        await self._bot.send_message(self._chat_id, text, parse_mode="HTML",
                                     disable_web_page_preview=True)

    async def send_changes(self, changes: list[Change]) -> None:
        groups: dict[str, list[Change]] = {}
        for c in changes:
            groups.setdefault(c.program_handle, []).append(c)
        for handle, group in groups.items():
            buf = ""
            for c in group:
                block = format_change(c)
                if buf and len(buf) + len(block) + 2 > _MAX:
                    await self.send_text(buf)
                    buf = ""
                buf = f"{buf}\n\n{block}" if buf else block
            if buf:
                await self.send_text(buf)
```

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** — `git commit -am "feat: add Telegram notifier and message formatting"`

---

### Task 11: Telegram bot (`bot.py`)

**Files:**
- Create: `h1monitor/bot.py`, `tests/test_bot.py`

**Interfaces:**
- Consumes: `store.Store`, `config.Settings`, `models` (Preferences, ChangeType).
- Produces (pure, unit-tested helpers + PTB wiring):
  - `is_owner(chat_id: int | None, store: Store, settings: Settings) -> bool` — true if `chat_id` equals stored owner id; if no owner stored yet, returns True **and** records `chat_id` as owner (first-contact capture) when settings has no fixed owner.
  - `build_config_keyboard(prefs: Preferences) -> InlineKeyboardMarkup` — one button per `ChangeType` (label = ✅/❌ + type value) with `callback_data=f"toggle:{type.value}"`, plus a row toggling `exclude_paused` (`callback_data="toggle:exclude_paused"`).
  - `apply_toggle(store: Store, data: str) -> Preferences` — mutates+saves prefs for `toggle:<key>` and returns new prefs.
  - `build_application(settings: Settings, store: Store) -> Application` — registers `CommandHandler`s (`start`, `setup`, `setapikey`, `config`, `programs`, `status`, `help`) and a `CallbackQueryHandler` for `toggle:`; wires post-capture message deletion for `/setapikey`.
- `parse_setapikey_args(text: str) -> tuple[str,str] | None` — splits `/setapikey <id> <token>`.

- [ ] **Step 1: Write failing tests** `tests/test_bot.py` (test the pure helpers; PTB handler bodies are integration-covered manually per spec §12)

```python
from cryptography.fernet import Fernet
from h1monitor.store import Store
from h1monitor.config import Settings
from h1monitor.bot import (
    is_owner, build_config_keyboard, apply_toggle, parse_setapikey_args,
)
from h1monitor.models import Preferences, ChangeType

def _settings():
    return Settings("bot", None, ":memory:", Fernet.generate_key(), None, None, None)

def _store(tmp_path):
    return Store(str(tmp_path / "b.db"), Fernet.generate_key())

def test_first_contact_captures_owner(tmp_path):
    st = _store(tmp_path)
    assert is_owner(555, st, _settings()) is True
    assert st.get_owner_chat_id() == 555
    assert is_owner(555, st, _settings()) is True
    assert is_owner(999, st, _settings()) is False

def test_fixed_owner_from_settings(tmp_path):
    st = _store(tmp_path)
    s = Settings("bot", 111, ":memory:", Fernet.generate_key(), None, None, None)
    assert is_owner(111, st, s) is True
    assert is_owner(222, st, s) is False

def test_parse_setapikey_args():
    assert parse_setapikey_args("/setapikey abc def") == ("abc", "def")
    assert parse_setapikey_args("/setapikey only") is None

def test_config_keyboard_has_button_per_type(tmp_path):
    kb = build_config_keyboard(Preferences.defaults())
    flat = [b.callback_data for row in kb.inline_keyboard for b in row]
    for t in ChangeType:
        assert f"toggle:{t.value}" in flat
    assert "toggle:exclude_paused" in flat

def test_apply_toggle_flips_and_persists(tmp_path):
    st = _store(tmp_path)
    st.save_preferences(Preferences.defaults())
    p = apply_toggle(st, "toggle:scope_added")
    assert p.is_type_enabled(ChangeType.SCOPE_ADDED) is False
    assert st.get_preferences().is_type_enabled(ChangeType.SCOPE_ADDED) is False

def test_apply_toggle_exclude_paused(tmp_path):
    st = _store(tmp_path)
    st.save_preferences(Preferences.defaults())
    p = apply_toggle(st, "toggle:exclude_paused")
    assert p.exclude_paused is False
```

- [ ] **Step 3: Implement `h1monitor/bot.py`** (helpers first — enough to pass tests — then PTB handlers using those helpers; handler code shown for completeness).

```python
from __future__ import annotations
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
)
from h1monitor.store import Store
from h1monitor.config import Settings
from h1monitor.models import Preferences, ChangeType


def is_owner(chat_id: int | None, store: Store, settings: Settings) -> bool:
    if chat_id is None:
        return False
    if settings.owner_chat_id is not None:
        return chat_id == settings.owner_chat_id
    stored = store.get_owner_chat_id()
    if stored is None:
        store.set_owner_chat_id(chat_id)
        return True
    return chat_id == stored


def parse_setapikey_args(text: str) -> tuple[str, str] | None:
    parts = (text or "").split()
    if len(parts) == 3:
        return parts[1], parts[2]
    return None


def build_config_keyboard(prefs: Preferences) -> InlineKeyboardMarkup:
    rows = []
    for t in ChangeType:
        mark = "✅" if prefs.is_type_enabled(t) else "❌"
        rows.append([InlineKeyboardButton(
            f"{mark} {t.value}", callback_data=f"toggle:{t.value}")])
    mark = "✅" if prefs.exclude_paused else "❌"
    rows.append([InlineKeyboardButton(
        f"{mark} exclude_paused", callback_data="toggle:exclude_paused")])
    return InlineKeyboardMarkup(rows)


def apply_toggle(store: Store, data: str) -> Preferences:
    key = data.split(":", 1)[1]
    prefs = store.get_preferences()
    if key == "exclude_paused":
        prefs.exclude_paused = not prefs.exclude_paused
    else:
        t = ChangeType(key)
        prefs.enabled[t] = not prefs.is_type_enabled(t)
    store.save_preferences(prefs)
    return prefs


def build_application(settings: Settings, store: Store) -> Application:
    app = Application.builder().token(settings.telegram_bot_token).build()

    def guard(update: Update) -> bool:
        chat = update.effective_chat
        return is_owner(chat.id if chat else None, store, settings)

    async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not guard(update):
            return
        has = store.get_h1_credentials() is not None
        msg = ("👋 h1monitor ready.\n"
               f"H1 credentials: {'set ✅' if has else 'not set ❌ — run /setup'}\n"
               "Use /config to choose what you receive.")
        await update.message.reply_text(msg)

    async def setapikey(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not guard(update):
            return
        parsed = parse_setapikey_args(update.message.text)
        try:
            await update.message.delete()
        except Exception:
            pass
        if not parsed:
            await update.effective_chat.send_message(
                "Usage: /setapikey <identifier> <token>")
            return
        store.set_h1_credentials(*parsed)
        await update.effective_chat.send_message(
            "🔐 H1 credentials saved (your message was deleted).")

    async def setup(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not guard(update):
            return
        await update.message.reply_text(
            "Send: /setapikey <identifier> <token>\n"
            "Your message is deleted immediately after capture.")

    async def config(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not guard(update):
            return
        await update.message.reply_text(
            "Toggle what you receive:",
            reply_markup=build_config_keyboard(store.get_preferences()))

    async def on_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not guard(update):
            return
        q = update.callback_query
        prefs = apply_toggle(store, q.data)
        await q.answer("Updated")
        await q.edit_message_reply_markup(build_config_keyboard(prefs))

    async def programs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not guard(update):
            return
        snap = store.load_api_snapshot()
        n = len(snap.programs) if snap else 0
        await update.message.reply_text(f"Monitoring {n} accessible program(s).")

    async def status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not guard(update):
            return
        prefs = store.get_preferences()
        has = store.get_h1_credentials() is not None
        await update.message.reply_text(
            f"Interval: {prefs.poll_interval_minutes}m | "
            f"H1 creds: {'yes' if has else 'no'} | "
            f"exclude_paused: {prefs.exclude_paused}")

    async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not guard(update):
            return
        await update.message.reply_text(
            "/start /setup /setapikey /config /programs /status /help")

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setup", setup))
    app.add_handler(CommandHandler("setapikey", setapikey))
    app.add_handler(CommandHandler("config", config))
    app.add_handler(CommandHandler("programs", programs))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CallbackQueryHandler(on_toggle, pattern=r"^toggle:"))
    return app
```

- [ ] **Step 4: Run — expect PASS.** `pytest tests/test_bot.py -q`
- [ ] **Step 5: Commit** — `git commit -am "feat: add Telegram bot commands and /config toggles"`

---

### Task 12: Poll cycles (`poller.py`, `directory_poller.py`)

**Files:**
- Create: `h1monitor/poller.py`, `h1monitor/directory_poller.py`, `tests/test_poller.py`

**Interfaces:**
- `poller.py` produces:
  - `async run_api_cycle(store: Store, client: H1Client, notifier: Notifier) -> None` — load previous snapshot; `snap = await client.fetch_snapshot(previous)`; if previous is None → save snap + send baseline text (`"Baseline established — watching N accessible programs."`) + `record_poll("api", time)`; else `changes = filter_changes(diff_api(previous, snap), store.get_preferences())`, `await notifier.send_changes(changes)`, save snap, record poll.
  - `async api_poll_loop(store, client_provider, notifier, stop: asyncio.Event, now=time.time) -> None` — loops: if creds → build client via `client_provider()` and run cycle (catch+alert-once on failure); sleep `prefs.poll_interval_minutes*60` or until `stop` set.
- `directory_poller.py` produces:
  - `async run_directory_cycle(store: Store, client: DirectoryClient, notifier: Notifier) -> None` — `progs = await client.fetch_all()`; `first = not store.has_directory_baseline()`; `changes = filter_changes(diff_directory(store.load_directory_handles(), progs, first), store.get_preferences())`; if first → send baseline (`"Tracking M directory programs."`); else `await notifier.send_changes(changes)`; `store.save_directory_handles({p.handle for p in progs})`; `record_poll("directory", time)`.
  - `async directory_poll_loop(store, client_provider, notifier, stop, now=time.time)`.
- Tests inject fakes for client/notifier/store; loops are exercised via a single `run_*_cycle` call (the loop wrapper is thin and manually smoke-tested).

- [ ] **Step 1: Write failing tests** `tests/test_poller.py`

```python
import pytest
from cryptography.fernet import Fernet
from h1monitor.store import Store
from h1monitor.models import Snapshot, Program, DirectoryProgram
from h1monitor.poller import run_api_cycle
from h1monitor.directory_poller import run_directory_cycle

class FakeNotifier:
    def __init__(self): self.changes = []; self.texts = []
    async def send_changes(self, changes): self.changes.extend(changes)
    async def send_text(self, text): self.texts.append(text)

class FakeH1:
    def __init__(self, snap): self._snap = snap
    async def fetch_snapshot(self, previous=None): return self._snap

class FakeDir:
    def __init__(self, progs): self._progs = progs
    async def fetch_all(self): return self._progs

def _store(tmp_path):
    return Store(str(tmp_path / "p.db"), Fernet.generate_key())

@pytest.mark.asyncio
async def test_api_first_run_baseline_no_changes(tmp_path):
    st = _store(tmp_path); n = FakeNotifier()
    snap = Snapshot({"acme": Program("acme", "Acme", "open", True, "USD", "p", {})})
    await run_api_cycle(st, FakeH1(snap), n)
    assert n.changes == []
    assert any("Baseline" in t for t in n.texts)
    assert st.load_api_snapshot() is not None

@pytest.mark.asyncio
async def test_api_second_run_emits_changes(tmp_path):
    st = _store(tmp_path); n = FakeNotifier()
    st.save_api_snapshot(Snapshot({"acme": Program("acme", "Acme", "open", True, "USD", "p", {})}))
    snap = Snapshot({"acme": Program("acme", "Acme", "paused", True, "USD", "p", {})})
    await run_api_cycle(st, FakeH1(snap), n)
    assert any("state" in c.summary for c in n.changes)

@pytest.mark.asyncio
async def test_directory_first_run_silent(tmp_path):
    st = _store(tmp_path); n = FakeNotifier()
    await run_directory_cycle(st, FakeDir([
        DirectoryProgram("a", "A", True, "open", "d", "u")]), n)
    assert n.changes == []
    assert st.has_directory_baseline() is True

@pytest.mark.asyncio
async def test_directory_second_run_new_program(tmp_path):
    st = _store(tmp_path); n = FakeNotifier()
    st.save_directory_handles({"a"})
    await run_directory_cycle(st, FakeDir([
        DirectoryProgram("a", "A", True, "open", "d", "u"),
        DirectoryProgram("vercel", "Vercel", True, "open", "2026-08-18", "u")]), n)
    assert [c.program_handle for c in n.changes] == ["vercel"]
```

- [ ] **Step 3: Implement `h1monitor/poller.py`**

```python
from __future__ import annotations
import asyncio, time
from h1monitor.store import Store
from h1monitor.notifier import Notifier
from h1monitor.differ import diff_api
from h1monitor.filters import filter_changes


async def run_api_cycle(store: Store, client, notifier: Notifier) -> None:
    previous = store.load_api_snapshot()
    snap = await client.fetch_snapshot(previous)
    if previous is None:
        store.save_api_snapshot(snap)
        await notifier.send_text(
            f"✅ Baseline established — watching {len(snap.programs)} accessible programs.")
        store.record_poll("api", time.time())
        return
    changes = filter_changes(diff_api(previous, snap), store.get_preferences())
    await notifier.send_changes(changes)
    store.save_api_snapshot(snap)
    store.record_poll("api", time.time())


async def api_poll_loop(store: Store, client_provider, notifier: Notifier,
                        stop: asyncio.Event) -> None:
    while not stop.is_set():
        creds = store.get_h1_credentials()
        if creds is None:
            if store.mark_alert_sent("api-no-creds"):
                await notifier.send_text("ℹ️ No H1 credentials yet — run /setup.")
        else:
            store.clear_alert("api-no-creds")
            client = client_provider(*creds)
            try:
                await run_api_cycle(store, client, notifier)
                store.clear_alert("api-fetch-failed")
            except Exception as e:  # noqa: BLE001
                if store.mark_alert_sent("api-fetch-failed"):
                    await notifier.send_text(f"⚠️ H1 API poll failed: {e}")
            finally:
                await client.aclose()
        interval = store.get_preferences().poll_interval_minutes * 60
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
```

- [ ] **Step 4: Implement `h1monitor/directory_poller.py`**

```python
from __future__ import annotations
import asyncio, time
from h1monitor.store import Store
from h1monitor.notifier import Notifier
from h1monitor.differ import diff_directory
from h1monitor.filters import filter_changes


async def run_directory_cycle(store: Store, client, notifier: Notifier) -> None:
    progs = await client.fetch_all()
    first = not store.has_directory_baseline()
    changes = filter_changes(
        diff_directory(store.load_directory_handles(), progs, first),
        store.get_preferences())
    if first:
        await notifier.send_text(f"✅ Tracking {len(progs)} directory programs.")
    else:
        await notifier.send_changes(changes)
    store.save_directory_handles({p.handle for p in progs})
    store.record_poll("directory", time.time())


async def directory_poll_loop(store: Store, client_provider, notifier: Notifier,
                              stop: asyncio.Event) -> None:
    while not stop.is_set():
        client = client_provider()
        try:
            await run_directory_cycle(store, client, notifier)
            store.clear_alert("dir-fetch-failed")
        except Exception as e:  # noqa: BLE001
            if store.mark_alert_sent("dir-fetch-failed"):
                await notifier.send_text(f"⚠️ Directory poll failed: {e}")
        finally:
            await client.aclose()
        interval = store.get_preferences().poll_interval_minutes * 60
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
```

- [ ] **Step 5: Run — expect PASS + Commit** — `git commit -am "feat: add API and directory poll cycles"`

---

### Task 13: Wiring, entrypoint & ops (`main.py`, docs)

**Files:**
- Create: `h1monitor/main.py`, `h1monitor/__main__.py`, `README.md`, `Dockerfile`, `systemd/h1monitor.service`
- Create: `tests/test_main_wiring.py`

**Interfaces:**
- Produces:
  - `build_runtime(settings: Settings, store: Store) -> tuple[Application, Notifier, client_providers]` — helper that constructs the bot Application, resolves owner chat id for the Notifier (falls back to seeding H1 creds from `settings.seed_h1_*` if present and store empty).
  - `async main_async(base_dir=".") -> None` — load settings, open store, seed creds if provided, build bot app + notifier, start `app`, launch `api_poll_loop` + `directory_poll_loop` under `asyncio.gather`, install SIGINT/SIGTERM handlers that set the stop event.
  - `run() -> None` — `asyncio.run(main_async())` (console-script entry).
  - `h1monitor/__main__.py` → `from h1monitor.main import run; run()`.
- Seeding: if `store.get_h1_credentials() is None` and `settings.seed_h1_username/token` set → `store.set_h1_credentials(...)`.

- [ ] **Step 1: Write failing test** `tests/test_main_wiring.py` (test the seeding helper without starting network loops)

```python
from cryptography.fernet import Fernet
from h1monitor.config import Settings
from h1monitor.store import Store
from h1monitor.main import seed_credentials_if_present

def test_seed_credentials(tmp_path):
    st = Store(str(tmp_path / "m.db"), Fernet.generate_key())
    s = Settings("bot", None, ":memory:", Fernet.generate_key(), "seedid", "seedtok", None)
    seed_credentials_if_present(st, s)
    assert st.get_h1_credentials() == ("seedid", "seedtok")

def test_seed_does_not_overwrite(tmp_path):
    st = Store(str(tmp_path / "m.db"), Fernet.generate_key())
    st.set_h1_credentials("existing", "creds")
    s = Settings("bot", None, ":memory:", Fernet.generate_key(), "seedid", "seedtok", None)
    seed_credentials_if_present(st, s)
    assert st.get_h1_credentials() == ("existing", "creds")
```

- [ ] **Step 3: Implement `h1monitor/main.py`**

```python
from __future__ import annotations
import asyncio, signal
from h1monitor.config import load_settings, Settings
from h1monitor.store import Store
from h1monitor.bot import build_application
from h1monitor.notifier import Notifier
from h1monitor.h1_client import H1Client
from h1monitor.directory_client import DirectoryClient
from h1monitor.poller import api_poll_loop
from h1monitor.directory_poller import directory_poll_loop


def seed_credentials_if_present(store: Store, settings: Settings) -> None:
    if (store.get_h1_credentials() is None
            and settings.seed_h1_username and settings.seed_h1_token):
        store.set_h1_credentials(settings.seed_h1_username, settings.seed_h1_token)


async def main_async(base_dir: str = ".") -> None:
    settings = load_settings(base_dir=base_dir)
    store = Store(settings.db_path, settings.secret_key)
    seed_credentials_if_present(store, settings)

    app = build_application(settings, store)
    stop = asyncio.Event()

    def _resolve_chat_id() -> int | None:
        return settings.owner_chat_id or store.get_owner_chat_id()

    async def _guarded_notifier_send(coro_factory):
        chat = _resolve_chat_id()
        if chat is None:
            return
        await coro_factory(chat)

    # Notifier resolves chat id lazily each send (owner may be captured on first /start)
    class _LazyNotifier(Notifier):
        def __init__(self, bot): self._bot = bot
        @property
        def _chat_id(self): return _resolve_chat_id()
        async def send_text(self, text):
            chat = _resolve_chat_id()
            if chat is None:
                return
            await self._bot.send_message(chat, text, parse_mode="HTML",
                                         disable_web_page_preview=True)

    notifier = _LazyNotifier(app.bot)

    def h1_provider(username, token):
        return H1Client(username, token)

    def dir_provider():
        return DirectoryClient(cookie=settings.directory_cookie)

    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    try:
        await asyncio.gather(
            api_poll_loop(store, h1_provider, notifier, stop),
            directory_poll_loop(store, dir_provider, notifier, stop),
        )
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        store.close()


def run() -> None:
    asyncio.run(main_async())
```

> Note: `_LazyNotifier` overrides `send_text`; `send_changes` (inherited) calls `send_text`, so lazy chat resolution flows through. `Notifier.__init__` signature is bypassed intentionally.

- [ ] **Step 4: Create `h1monitor/__main__.py`**

```python
from h1monitor.main import run

if __name__ == "__main__":
    run()
```

- [ ] **Step 5: Write `README.md`, `Dockerfile`, `systemd/h1monitor.service`**

`Dockerfile`:
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY h1monitor ./h1monitor
RUN pip install --no-cache-dir .
CMD ["python", "-m", "h1monitor"]
```

`systemd/h1monitor.service`:
```ini
[Unit]
Description=h1monitor — HackerOne program change monitor
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/h1monitor
EnvironmentFile=/opt/h1monitor/.env
ExecStart=/usr/bin/python -m h1monitor
Restart=on-failure
RestartSec=15

[Install]
WantedBy=multi-user.target
```

`README.md` — quickstart: create Telegram bot with @BotFather → put token in `.env` → `pip install -e .` → `python -m h1monitor` → DM the bot `/start` then `/setapikey <id> <token>` → `/config`. Document all env vars (spec §10), the security model (spec §9), and the directory-feed caveat (spec §4b).

- [ ] **Step 6: Run full suite — expect ALL PASS.** `pytest -q`
- [ ] **Step 7: Commit** — `git commit -am "feat: wire daemon entrypoint, Docker, systemd, and docs"`

---

## Self-Review

**Spec coverage:**
- §4a REST source → Tasks 8 (client), 5 (differ), 12 (cycle). ✓
- §4b directory source → Tasks 9 (client), 6 (differ), 12 (cycle). ✓
- §5 all 8 change types → Task 5 (7 types) + Task 6 (`new_public_program`). ✓
- §6 module layout → Tasks 2–13 map 1:1 to modules. ✓
- §7 preferences & filtering (allow/deny, exclude_paused incl. became-paused, toggles) → Task 7. ✓
- §8 commands, single-chat delivery, category labels, new-program format → Tasks 10, 11. ✓
- §9 security (owner-only, auto-delete key, Fernet-at-rest, 0600) → Tasks 3, 4, 11. ✓
- §10 config/env → Tasks 3, 13. ✓
- §11 error handling (silent baselines, retain-on-failure, alert-once) → Tasks 8, 12. ✓
- §12 testing → tests in every task. ✓
- §13 deliverables (pyproject, .env.example, README, Dockerfile, systemd) → Tasks 1, 13. ✓

**Placeholder scan:** no TBD/TODO; every code step has real code. ✓

**Type consistency:** `Change` field names (`types`, `program_handle`, `program_name`, `submission_state`, `summary`, `details`, `directory`) consistent across differ/filters/notifier/bot. `ChangeType` values match Global Constraints. `Store` method names used by pollers (`load_api_snapshot`, `save_api_snapshot`, `has_directory_baseline`, `load_directory_handles`, `save_directory_handles`, `get_preferences`, `get_h1_credentials`, `mark_alert_sent`, `clear_alert`, `record_poll`) all defined in Task 4. `filter_changes`, `diff_api`, `diff_directory` signatures consistent. ✓

**Known follow-up (spec §14):** the directory GraphQL query/auth is implemented to the documented shape but flagged for a live spike; if the anonymous session or query shape differs, only `directory_client.py` (Task 9) changes — the parse helper and pipeline stay stable.
