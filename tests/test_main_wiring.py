from cryptography.fernet import Fernet

from h1monitor.config import Settings, load_dotenv
from h1monitor.store import Store
from h1monitor.main import seed_credentials_if_present, ensure_bot_token


def test_seed_credentials(tmp_path):
    st = Store(str(tmp_path / "m.db"), Fernet.generate_key())
    s = Settings("bot", None, ":memory:", Fernet.generate_key(), "seedid", "seedtok", None)
    seed_credentials_if_present(st, s)
    assert st.get_h1_credentials() == ("seedid", "seedtok")


def test_seed_does_not_overwrite(tmp_path):
    st = Store(str(tmp_path / "m.db"), Fernet.generate_key())
    st.set_h1_credentials("existing", "creds")
    s = Settings("bot", None, ":memory:", Fernet.generate_key(), "seedid", "seedtok", None)
    seed_credentials_if_present(st, s)
    assert st.get_h1_credentials() == ("existing", "creds")


def test_ensure_bot_token_prompts_and_writes(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    ensure_bot_token(
        str(tmp_path), input_fn=lambda _: "botTOKEN123", isatty=True, out=lambda *a: None
    )
    assert load_dotenv(str(tmp_path))["TELEGRAM_BOT_TOKEN"] == "botTOKEN123"


def test_ensure_bot_token_skips_when_present(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "already")
    calls = {"n": 0}

    def boom(_):
        calls["n"] += 1
        return "x"

    ensure_bot_token(str(tmp_path), input_fn=boom, isatty=True, out=lambda *a: None)
    assert calls["n"] == 0
    assert not (tmp_path / ".env").exists()


def test_ensure_bot_token_noninteractive_no_write(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    ensure_bot_token(
        str(tmp_path), input_fn=lambda _: "x", isatty=False, out=lambda *a: None
    )
    assert not (tmp_path / ".env").exists()


import pytest


@pytest.mark.asyncio
async def test_lazynotifier_swallows_send_errors():
    from h1monitor.main import LazyNotifier

    class BoomBot:
        async def send_message(self, *a, **k):
            raise RuntimeError("telegram unreachable")

    n = LazyNotifier(BoomBot(), lambda: 123)
    assert await n.send_text("hi") is False   # swallowed, never raised


@pytest.mark.asyncio
async def test_lazynotifier_returns_false_without_owner():
    from h1monitor.main import LazyNotifier

    class Bot:
        async def send_message(self, *a, **k):
            return None

    assert await LazyNotifier(Bot(), lambda: None).send_text("hi") is False


@pytest.mark.asyncio
async def test_lazynotifier_delivers_returns_true():
    from h1monitor.main import LazyNotifier
    sent = []

    class Bot:
        async def send_message(self, chat, text, **k):
            sent.append((chat, text))

    assert await LazyNotifier(Bot(), lambda: 7).send_text("hi") is True
    assert sent == [(7, "hi")]
