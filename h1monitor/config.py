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


def _env_path(base_dir: str) -> Path:
    return Path(base_dir) / ".env"


def _parse_env_text(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            out[key] = value
    return out


def load_dotenv(base_dir: str) -> dict[str, str]:
    path = _env_path(base_dir)
    if not path.exists():
        return {}
    return _parse_env_text(path.read_text())


def upsert_env_var(base_dir: str, key: str, value: str) -> None:
    """Set KEY=value in <base_dir>/.env, preserving other lines. Creates the
    file with mode 0600 if it does not exist."""
    path = _env_path(base_dir)
    newfile = not path.exists()
    lines = path.read_text().splitlines() if not newfile else []
    replaced = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"{key}=") or stripped.startswith(f"{key} ="):
            lines[i] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n")
    if newfile:
        os.chmod(path, 0o600)


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
    # When no explicit env is given, merge the .env file with the real
    # environment (real environment variables win).
    if env is None:
        env = {**load_dotenv(base_dir), **os.environ}
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
