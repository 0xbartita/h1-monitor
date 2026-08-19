import stat

import pytest

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
    s = load_settings(
        env={"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_OWNER_CHAT_ID": "42"},
        base_dir=str(tmp_path),
    )
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
