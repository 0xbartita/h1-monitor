from cryptography.fernet import Fernet

from h1monitor.store import Store
from h1monitor.config import Settings
from h1monitor.bot import (
    is_owner, build_config_keyboard, apply_toggle, parse_setapikey_args,
    change_type_label, programs_text, status_text, help_text, setup_text,
    start_text, BOT_COMMANDS, config_prompt,
    format_interval, step_interval, apply_interval_step,
    estimate_sweep_minutes, recommend_private_interval,
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


def test_private_presets_stay_above_sweep_time():
    # private floor must clear a single ~10-13 min sweep so scans never overlap
    assert min(PRIVATE_INTERVALS) >= 15
    assert min(PUBLIC_INTERVALS) >= 1


def test_recommend_private_interval_scales_with_account_size():
    # tiny account -> floored at the smallest preset
    assert recommend_private_interval(5, 50) == min(PRIVATE_INTERVALS)
    # large account -> a longer gap, and never faster than a full sweep
    big = recommend_private_interval(458, 14950)
    assert big in PRIVATE_INTERVALS
    assert big > min(PRIVATE_INTERVALS)
    assert big >= estimate_sweep_minutes(458, 14950)


def test_config_prompt_includes_private_recommendation():
    txt = config_prompt(458, 14950)
    assert "458" in txt and "suggest" in txt
    # no private programs yet -> no recommendation line
    assert "suggest" not in config_prompt(0, 0)


def test_message_text_is_html_safe():
    # literal angle brackets must be escaped so Telegram HTML parsing succeeds
    assert "&lt;username&gt;" in setup_text() and "<username>" not in setup_text()
    assert "&amp;" in help_text()
    assert "<b>" in start_text(False)  # actually styled


def test_setup_text_links_the_api_token_page():
    txt = setup_text()
    assert 'href="https://hackerone.com/settings/api_token/edit"' in txt
    assert "username" in txt and "identifier" not in txt


def test_bot_commands_cover_all_handlers():
    names = {c.command for c in BOT_COMMANDS}
    assert names == {
        "start", "setup", "setapikey", "config", "programs", "status", "help",
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
