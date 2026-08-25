from cryptography.fernet import Fernet

from h1monitor.store import Store
from h1monitor.config import Settings
from h1monitor.bot import (
    claim_if_unowned, unclaimed, already_claimed_text,
    is_owner, build_config_keyboard, apply_toggle, parse_setup_args,
    change_type_label, status_text, help_text, setup_text,
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


def test_status_text_folds_in_program_and_scope_counts():
    # /programs was removed; its counts now live in /status
    txt = status_text(Preferences.defaults(), True, 449, 458, 11296, 14950)
    assert "449" in txt and "458" in txt
    assert "11,296" in txt and "14,950" in txt  # thousands separators
    assert "Public" in txt and "Private" in txt


def test_status_text_shows_both_intervals_and_creds():
    txt = status_text(Preferences.defaults(), True, 0, 0, 0, 0)
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
        "start", "setup", "config", "status", "help",
    }
    assert all(c.description for c in BOT_COMMANDS)  # every command has a description


def test_programs_command_is_fully_removed():
    assert "/programs" not in help_text()
    assert "programs" not in {c.command for c in BOT_COMMANDS}


def _settings():
    return Settings("bot", None, ":memory:", Fernet.generate_key(), None, None, None)


def _store(tmp_path):
    return Store(str(tmp_path / "b.db"), Fernet.generate_key())


def test_first_start_claims_the_bot(tmp_path):
    st, s = _store(tmp_path), _settings()
    assert unclaimed(st, s) is True
    assert claim_if_unowned(555, st, s) is True
    assert is_owner(555, st, s) is True
    assert st.get_owner_chat_id() == 555


def test_a_second_chat_cannot_take_it(tmp_path):
    st, s = _store(tmp_path), _settings()
    claim_if_unowned(555, st, s)
    assert claim_if_unowned(999, st, s) is False
    assert is_owner(999, st, s) is False
    assert st.get_owner_chat_id() == 555


def test_a_group_chat_cannot_claim_the_bot(tmp_path):
    # Claiming in a group would hand every private-program alert to everyone in
    # it, so only a one-to-one chat may claim.
    st, s = _store(tmp_path), _settings()
    assert claim_if_unowned(-100, st, s, private=False) is False
    assert st.get_owner_chat_id() is None


def test_other_commands_never_claim(tmp_path):
    # Only /start claims. is_owner is a pure check, so /status from a stranger
    # must not quietly make them the owner.
    st, s = _store(tmp_path), _settings()
    assert is_owner(999, st, s) is False
    assert st.get_owner_chat_id() is None


def test_pinned_owner_in_env_cannot_be_claimed_away(tmp_path):
    st = _store(tmp_path)
    s = Settings("bot", 111, ":memory:", Fernet.generate_key(), None, None, None)
    assert unclaimed(st, s) is False
    assert claim_if_unowned(999, st, s) is False
    assert is_owner(111, st, s) is True


def test_clearing_the_owner_allows_a_fresh_claim(tmp_path):
    # The escape hatch behind --reset-owner.
    st, s = _store(tmp_path), _settings()
    claim_if_unowned(999, st, s)
    st.clear_owner_chat_id()
    assert unclaimed(st, s) is True
    assert claim_if_unowned(555, st, s) is True
    assert is_owner(555, st, s) is True


def test_refusal_text_does_not_leak_anything(tmp_path):
    assert "already in use" in already_claimed_text()


def test_fixed_owner_from_settings(tmp_path):
    st = _store(tmp_path)
    s = Settings("bot", 111, ":memory:", Fernet.generate_key(), None, None, None)
    assert is_owner(111, st, s) is True
    assert is_owner(222, st, s) is False


def test_parse_setup_args():
    assert parse_setup_args("/setup abc def") == ("abc", "def")
    assert parse_setup_args("/setup only") is None
    assert parse_setup_args("/setup") is None


def test_setup_text_uses_the_setup_command_not_setapikey():
    txt = setup_text()
    assert "/setup" in txt and "setapikey" not in txt


def test_start_text_prompts_setup_in_both_states():
    from h1monitor.bot import start_text
    assert "/setup" in start_text(False)  # not connected -> connect call-to-action
    assert "/setup" in start_text(True)   # connected -> still points at /setup


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


def test_saving_credentials_wakes_the_private_loop(tmp_path):
    """The whole point: an API key arriving must kick the sleeping sweep."""
    from h1monitor.bot import save_h1_credentials
    st = _store(tmp_path)
    rung = []
    save_h1_credentials(st, "id", "tok", wake=rung.append)
    assert st.get_h1_credentials() == ("id", "tok")
    assert rung == ["private"]


def test_saving_credentials_works_without_a_waker(tmp_path):
    """Defensive: no waker wired (tests, seeding) must not crash the save."""
    from h1monitor.bot import save_h1_credentials
    st = _store(tmp_path)
    save_h1_credentials(st, "id", "tok")
    assert st.get_h1_credentials() == ("id", "tok")


def test_interval_change_wakes_the_loop_it_affects(tmp_path):
    st = _store(tmp_path)
    st.save_preferences(Preferences.defaults())
    rung = []
    apply_interval_step(st, "intv:private:-", wake=rung.append)
    assert rung == ["private"]
    rung.clear()
    apply_interval_step(st, "intv:public:+", wake=rung.append)
    assert rung == ["public"]


def test_status_carries_no_version_stamp(tmp_path):
    # /status answers "what is it watching right now". The version lives on
    # /start, so it is not repeated under every status check.
    txt = status_text(Preferences.defaults(), True, 1, 1, 1, 1, version="0.1.0")
    assert "0.1.0" not in txt
    assert "Version" not in txt


def test_status_stays_quiet_about_updates(tmp_path):
    txt = status_text(
        Preferences.defaults(), True, 1, 1, 1, 1,
        version="0.1.0", latest=("v0.2.0", "https://example.com/rel"),
    )
    assert "0.2.0" not in txt and "href" not in txt


def test_start_shows_the_running_version():
    assert "0.1.0" in start_text(True, version="0.1.0")


def test_start_offers_a_tappable_upgrade_when_one_exists():
    txt = start_text(True, version="0.1.0",
                     latest=("v0.2.0", "https://example.com/rel"))
    assert 'href="https://example.com/rel"' in txt
    assert "0.2.0" in txt
    # the release page explains how to upgrade; the bot doesn't guess
    for cmd in ("docker pull", "systemctl", "install.sh"):
        assert cmd not in txt


def test_start_does_not_advertise_the_command_menu():
    # Telegram already shows the command list beside the input box.
    assert "Tap the menu" not in start_text(True)


def test_start_mentions_an_available_update():
    txt = start_text(True, version="0.1.0", latest=("v0.2.0", "https://example.com/rel"))
    assert "0.2.0" in txt and "href" in txt


def test_start_says_nothing_about_updates_when_current():
    txt = start_text(True, version="0.2.0", latest=("v0.2.0", "https://example.com/rel"))
    assert "0.2.0" in txt
    assert "href=\"https://example.com/rel\"" not in txt


def test_start_points_people_at_the_updates_channel():
    txt = start_text(True)
    assert "h1_monitor" in txt
    assert 'href="https://t.me/h1_monitor"' in txt


def test_help_points_people_at_the_updates_channel():
    assert 'href="https://t.me/h1_monitor"' in help_text()


def test_a_group_is_told_to_use_a_private_chat_not_that_it_is_taken():
    # A fresh bot refusing a group must not claim to be "already in use" — that
    # is false, and sends the operator looking for a hijack that never happened.
    from h1monitor.bot import use_private_chat_text
    assert "privately" in use_private_chat_text()
    assert "already in use" not in use_private_chat_text()


def test_status_says_scanning_while_the_first_private_sweep_runs():
    # The private snapshot is written once, at the end of a 10-15 minute sweep,
    # so a bare "0 programs" sits there the whole time and reads as a dead bot.
    txt = status_text(Preferences.defaults(), True, 449, 0, 41214, 0,
                      private_ready=False)
    assert "scanning now" in txt
    assert "0</b> programs" not in txt


def test_status_shows_the_real_count_once_the_sweep_has_finished():
    txt = status_text(Preferences.defaults(), True, 449, 12, 41214, 900,
                      private_ready=True)
    assert "scanning now" not in txt
    assert "12" in txt


def test_status_does_not_claim_to_be_scanning_without_a_key():
    # No API key means nothing to scan — don't imply work is happening.
    txt = status_text(Preferences.defaults(), False, 449, 0, 41214, 0,
                      private_ready=False)
    assert "scanning now" not in txt


def test_setup_confirmation_sets_expectations():
    from h1monitor.bot import setup_saved
    txt = setup_saved()
    assert "Scanning your private programs now" in txt
    assert "10-15" in txt
    # Must match what /status actually says, or the two contradict each other.
    assert "scanning now" in txt


def test_setup_does_not_claim_a_delete_that_failed():
    # Telegram can refuse the delete. Saying "your message was deleted" anyway
    # leaves the API key on screen while the user believes it is gone.
    from h1monitor.bot import setup_saved, setup_usage
    saved = setup_saved(deleted=False)
    assert "was deleted" not in saved
    assert "delete it yourself" in saved.lower()
    # Still saved, and still says what happens next.
    assert "API key saved" in saved
    assert "Scanning your private programs now" in saved
    # Same when the arguments did not parse — the message still carried a key.
    assert "delete it yourself" in setup_usage(deleted=False).lower()
    assert "delete it yourself" not in setup_usage(deleted=True).lower()


def test_status_says_scanning_while_the_public_baseline_is_missing():
    # Upgrading drops the public baseline on purpose, which reopens the same
    # window: 0 programs, no explanation, looks broken.
    txt = status_text(Preferences.defaults(), True, 0, 12, 0, 900,
                      public_ready=False)
    assert "Public — <b>scanning now</b>" in txt
    assert "12" in txt          # private is unaffected


def test_status_shows_public_counts_once_the_sweep_lands():
    txt = status_text(Preferences.defaults(), True, 450, 12, 41214, 900)
    assert "scanning now" not in txt
    assert "450" in txt and "41,214" in txt
