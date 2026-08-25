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


def test_transient_network_blip_logs_one_line_not_a_traceback():
    """A DNS blip makes python-telegram-bot dump a multi-page traceback per
    retry. It recovers on its own, so it should read as one warning line —
    a wall of stack traces looks like a crash and isn't."""
    import logging
    from telegram.error import NetworkError
    from h1monitor.main import NetworkNoiseFilter

    exc = NetworkError(
        "httpx.ConnectError: [Errno -3] Temporary failure in name resolution"
    )
    rec = logging.LogRecord(
        "telegram.ext.Updater", logging.ERROR, __file__, 1,
        "Exception happened while polling for updates.", (), (type(exc), exc, None),
    )
    assert NetworkNoiseFilter().filter(rec) is True   # kept, never swallowed
    assert rec.exc_info is None                       # but without the stack dump
    assert rec.levelno == logging.WARNING             # not something to act on
    msg = rec.getMessage()
    assert "retrying" in msg.lower()
    assert "name resolution" in msg                   # the cause still shows


def test_real_errors_keep_their_traceback():
    """Only transient network errors are quietened — anything else must keep
    its stack trace and its ERROR level."""
    import logging
    from h1monitor.main import NetworkNoiseFilter

    exc = ValueError("genuinely broken")
    rec = logging.LogRecord(
        "telegram.ext.Updater", logging.ERROR, __file__, 1,
        "Exception happened while polling for updates.", (), (type(exc), exc, None),
    )
    assert NetworkNoiseFilter().filter(rec) is True
    assert rec.exc_info is not None
    assert rec.levelno == logging.ERROR
