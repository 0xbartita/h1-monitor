import sqlite3
import stat

import pytest
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
    row = sqlite3.connect(str(tmp_path / "t.db")).execute(
        "select value from credentials where name='h1_token'"
    ).fetchone()
    assert "tok456" not in (row[0] if row else "")


def test_owner_chat_id_roundtrip(store):
    assert store.get_owner_chat_id() is None
    store.set_owner_chat_id(99)
    assert store.get_owner_chat_id() == 99


def test_api_snapshot_roundtrip(store):
    prog = Program(
        "acme", "Acme", "open", True, "USD", "policy",
        {"URL:a.com": Scope("URL", "a.com", True, True, "high",
                            None, None, None, None, "2026-01-01")},
    )
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
