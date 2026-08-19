from cryptography.fernet import Fernet

from h1monitor.store import Store
from h1monitor.config import Settings
from h1monitor.bot import (
    is_owner, build_config_keyboard, apply_toggle, parse_setapikey_args,
    change_type_label, programs_text, status_text, help_text, setup_text,
    start_text, BOT_COMMANDS,
    format_interval, step_interval, apply_interval_step,
    parse_setinterval_args, apply_setinterval, INTERVAL_BOUNDS,
    PUBLIC_INTERVALS, PRIVATE_INTERVALS,
)
from h1monitor.models import Preferences, ChangeType


def test_change_type_label_covers_every_type():
    for t in ChangeType:
        label = change_type_label(t)
        assert label and label != t.value  # human phrase, not the raw enum value


def test_programs_text_formats_counts_with_separators():
    txt = programs_text(449, 458, 11296, 14950)
    assert "449" in txt and "458" in txt
    assert "11,296" in txt and "14,950" in txt  # thousands separators
    assert "Public" in txt and "Private" in txt


def test_status_text_shows_both_intervals_and_creds():
    txt = status_text(Preferences.defaults(), True)
    # defaults: public 30 -> "30 min", private 120 -> "2 h"
    assert "30 min" in txt and "2 h" in txt and "connected" in txt


def test_format_interval_renders_minutes_hours_days():
    assert format_interval(15) == "15 min"
    assert format_interval(30) == "30 min"
    assert format_interval(60) == "1 h"
    assert format_interval(120) == "2 h"
    assert format_interval(90) == "1 h 30 min"
    assert format_interval(1440) == "1 day"
    assert format_interval(2880) == "2 days"


def test_step_interval_moves_through_presets_and_clamps():
    presets = [15, 30, 60, 120]
    assert step_interval(30, presets, +1) == 60
    assert step_interval(30, presets, -1) == 15
    assert step_interval(120, presets, +1) == 120  # clamps at top
    assert step_interval(15, presets, -1) == 15  # clamps at bottom
    # off-preset value snaps to nearest preset in the step direction
    assert step_interval(45, presets, +1) == 60
    assert step_interval(45, presets, -1) == 30


def test_config_keyboard_has_interval_steppers(tmp_path):
    kb = build_config_keyboard(Preferences.defaults())
    flat = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "intv:public:+" in flat and "intv:public:-" in flat
    assert "intv:private:+" in flat and "intv:private:-" in flat


def test_apply_interval_step_changes_and_persists(tmp_path):
    st = _store(tmp_path)
    st.save_preferences(Preferences.defaults())
    p = apply_interval_step(st, "intv:public:+")  # 30 -> 60
    assert p.poll_interval_minutes == 60
    assert st.get_preferences().poll_interval_minutes == 60
    p = apply_interval_step(st, "intv:private:-")  # 120 -> 60
    assert p.private_interval_minutes == 60
    assert st.get_preferences().private_interval_minutes == 60


def test_parse_setinterval_args():
    assert parse_setinterval_args("/setinterval public 45") == ("public", 45)
    assert parse_setinterval_args("/setinterval PRIVATE 90") == ("private", 90)
    assert parse_setinterval_args("/setinterval public") is None
    assert parse_setinterval_args("/setinterval bogus 45") is None
    assert parse_setinterval_args("/setinterval public abc") is None


def test_apply_setinterval_sets_custom_value(tmp_path):
    st = _store(tmp_path)
    st.save_preferences(Preferences.defaults())
    p = apply_setinterval(st, "private", 45)
    assert p.private_interval_minutes == 45
    assert st.get_preferences().private_interval_minutes == 45


def test_interval_bounds_floor_private_above_sweep_time():
    lo_pub, _ = INTERVAL_BOUNDS["public"]
    lo_priv, _ = INTERVAL_BOUNDS["private"]
    # private floor must exceed a single ~10-13 min sweep so scans never overlap
    assert lo_priv >= 15
    assert lo_pub >= 1
    # presets must respect their own floors
    assert min(PUBLIC_INTERVALS) >= lo_pub
    assert min(PRIVATE_INTERVALS) >= lo_priv


def test_message_text_is_html_safe():
    # literal angle brackets must be escaped so Telegram HTML parsing succeeds
    assert "&lt;identifier&gt;" in setup_text() and "<identifier>" not in setup_text()
    assert "&amp;" in help_text()
    assert "<b>" in start_text(False)  # actually styled


def test_bot_commands_cover_all_handlers():
    names = {c.command for c in BOT_COMMANDS}
    assert names == {
        "start", "setup", "setapikey", "config", "programs", "status",
        "setinterval", "help",
    }
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
