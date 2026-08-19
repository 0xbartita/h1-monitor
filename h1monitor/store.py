from __future__ import annotations

import json
import os
import sqlite3
import threading

from h1monitor.models import Snapshot, Program, Scope, Preferences
from h1monitor.config import encrypt, decrypt


def _scope_to_dict(s: Scope) -> dict:
    return {
        "asset_type": s.asset_type,
        "asset_identifier": s.asset_identifier,
        "eligible_for_bounty": s.eligible_for_bounty,
        "eligible_for_submission": s.eligible_for_submission,
        "max_severity": s.max_severity,
        "instruction": s.instruction,
        "confidentiality_requirement": s.confidentiality_requirement,
        "integrity_requirement": s.integrity_requirement,
        "availability_requirement": s.availability_requirement,
        "updated_at": s.updated_at,
        "reference": s.reference,
    }


def _scope_from_dict(d: dict) -> Scope:
    return Scope(
        d["asset_type"], d["asset_identifier"], d["eligible_for_bounty"],
        d["eligible_for_submission"], d.get("max_severity"), d.get("instruction"),
        d.get("confidentiality_requirement"), d.get("integrity_requirement"),
        d.get("availability_requirement"), d.get("updated_at"), d.get("reference"),
    )


def _snapshot_to_json(s: Snapshot) -> str:
    return json.dumps(
        {
            h: {
                "handle": p.handle,
                "name": p.name,
                "submission_state": p.submission_state,
                "offers_bounties": p.offers_bounties,
                "currency": p.currency,
                "policy": p.policy,
                "started_accepting_at": p.started_accepting_at,
                "scopes": {k: _scope_to_dict(v) for k, v in p.scopes.items()},
            }
            for h, p in s.programs.items()
        }
    )


def _snapshot_from_json(raw: str) -> Snapshot:
    d = json.loads(raw)
    progs = {}
    for h, pd in d.items():
        progs[h] = Program(
            pd["handle"], pd["name"], pd.get("submission_state"),
            pd.get("offers_bounties"), pd.get("currency"), pd.get("policy"),
            {k: _scope_from_dict(v) for k, v in pd.get("scopes", {}).items()},
            pd.get("started_accepting_at"),
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
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
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
                    (name, encrypt(self._key, val)),
                )
            self._db.commit()

    def get_h1_credentials(self) -> tuple[str, str] | None:
        rows = dict(self._db.execute("SELECT name,value FROM credentials").fetchall())
        if "h1_username" in rows and "h1_token" in rows:
            return (
                decrypt(self._key, rows["h1_username"]),
                decrypt(self._key, rows["h1_token"]),
            )
        return None

    # --- owner ---
    def get_owner_chat_id(self) -> int | None:
        raw = self._get("owner_chat_id")
        return int(raw) if raw else None

    def set_owner_chat_id(self, chat_id: int) -> None:
        self._set("owner_chat_id", str(chat_id))

    # --- snapshots (kind is "public" or "private") ---
    def load_snapshot(self, kind: str) -> Snapshot | None:
        raw = self._get(f"snapshot:{kind}")
        return _snapshot_from_json(raw) if raw else None

    def save_snapshot(self, kind: str, s: Snapshot) -> None:
        self._set(f"snapshot:{kind}", _snapshot_to_json(s))

    def has_baseline(self, kind: str) -> bool:
        return self._get(f"snapshot:{kind}") is not None

    # --- poll metadata ---
    def record_poll(self, source: str, ts: float) -> None:
        self._set(f"last_poll:{source}", str(ts))

    def get_last_poll(self, source: str) -> float | None:
        raw = self._get(f"last_poll:{source}")
        return float(raw) if raw else None

    # --- alert dedup ---
    def mark_alert_sent(self, key: str) -> bool:
        with self._lock:
            cur = self._db.execute("INSERT OR IGNORE INTO alerts(key) VALUES(?)", (key,))
            self._db.commit()
            return cur.rowcount == 1

    def clear_alert(self, key: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM alerts WHERE key=?", (key,))
            self._db.commit()

    def close(self) -> None:
        self._db.close()
