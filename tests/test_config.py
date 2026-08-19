import stat

import pytest

from h1monitor.config import (
    load_settings, encrypt, decrypt, ConfigError, load_dotenv, upsert_env_var,
)


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


def test_load_settings_reads_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    (tmp_path / ".env").write_text(
        "TELEGRAM_BOT_TOKEN=fromfile\n# a comment\nH1_API_USERNAME=u\n"
    )
    s = load_settings(base_dir=str(tmp_path))  # env=None -> reads .env
    assert s.telegram_bot_token == "fromfile"
    assert s.seed_h1_username == "u"


def test_real_env_overrides_dotenv(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fromenv")
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=fromfile\n")
    s = load_settings(base_dir=str(tmp_path))
    assert s.telegram_bot_token == "fromenv"


def test_upsert_env_var_creates_and_updates(tmp_path):
    upsert_env_var(str(tmp_path), "TELEGRAM_BOT_TOKEN", "a")
    p = tmp_path / ".env"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert load_dotenv(str(tmp_path))["TELEGRAM_BOT_TOKEN"] == "a"
    upsert_env_var(str(tmp_path), "TELEGRAM_BOT_TOKEN", "b")
    assert load_dotenv(str(tmp_path))["TELEGRAM_BOT_TOKEN"] == "b"
    assert p.read_text().count("TELEGRAM_BOT_TOKEN=") == 1  # updated, not duplicated


def test_upsert_env_var_preserves_other_lines(tmp_path):
    (tmp_path / ".env").write_text("H1_API_USERNAME=keepme\n")
    upsert_env_var(str(tmp_path), "TELEGRAM_BOT_TOKEN", "a")
    env = load_dotenv(str(tmp_path))
    assert env["H1_API_USERNAME"] == "keepme"
    assert env["TELEGRAM_BOT_TOKEN"] == "a"
