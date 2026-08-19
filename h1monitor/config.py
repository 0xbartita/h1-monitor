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
