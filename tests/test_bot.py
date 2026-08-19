from cryptography.fernet import Fernet

from h1monitor.store import Store
from h1monitor.config import Settings
from h1monitor.bot import (
    is_owner, build_config_keyboard, apply_toggle, parse_setapikey_args, BOT_COMMANDS,
)
from h1monitor.models import Preferences, ChangeType


def test_bot_commands_cover_all_handlers():
    names = {c.command for c in BOT_COMMANDS}
    assert names == {"start", "setup", "setapikey", "config", "programs", "status", "help"}
    assert all(c.description for c in BOT_COMMANDS)  # every command has a description


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
