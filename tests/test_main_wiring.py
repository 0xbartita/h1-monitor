import pytest
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


# --- claiming: the code has to reach the operator, and only the operator -----

def _env(tmp_path, token="123:ABC"):
    (tmp_path / ".env").write_text(f"TELEGRAM_BOT_TOKEN={token}\n")
    return str(tmp_path)


def test_claim_banner_tells_you_what_to_send():
    from h1monitor.main import claim_banner
    b = claim_banner()
    assert "/start" in b
    assert "No owner yet" in b


def test_reset_owner_lets_the_next_start_claim_it(tmp_path, capsys):
    from h1monitor.main import reset_owner
    from h1monitor.config import load_settings
    from h1monitor.store import Store
    from h1monitor.bot import unclaimed

    base = _env(tmp_path)
    s = load_settings(base_dir=base)
    st = Store(s.db_path, s.secret_key)
    st.set_owner_chat_id(999)          # the wrong chat got there first
    st.close()

    assert reset_owner(base) == 0
    assert "Owner cleared" in capsys.readouterr().out
    st = Store(s.db_path, s.secret_key)
    assert unclaimed(st, s) is True
    st.close()


def test_reset_owner_refuses_when_the_owner_is_pinned_in_env(tmp_path, capsys):
    (tmp_path / ".env").write_text(
        "TELEGRAM_BOT_TOKEN=123:ABC\nTELEGRAM_OWNER_CHAT_ID=111\n")
    from h1monitor.main import reset_owner
    assert reset_owner(str(tmp_path)) == 1
    assert "TELEGRAM_OWNER_CHAT_ID" in capsys.readouterr().out


def test_reset_owner_reports_a_missing_token_cleanly(tmp_path, capsys):
    from h1monitor.main import reset_owner
    assert reset_owner(str(tmp_path)) == 1
    assert "TELEGRAM_BOT_TOKEN" in capsys.readouterr().err


def test_a_rejected_bot_token_explains_itself(monkeypatch, capsys):
    # The likeliest first-run mistake is a mistyped token. PTB answers with two
    # chained tracebacks ending in "Unauthorized", which helps nobody.
    from telegram.error import InvalidToken
    import h1monitor.main as m

    async def boom():
        raise InvalidToken("The token `123:ABC` was rejected by the server.")

    monkeypatch.setattr(m, "main_async", boom)
    with pytest.raises(SystemExit) as e:
        m.run()
    assert e.value.code == 1
    err = capsys.readouterr().err
    assert "rejected your bot token" in err
    assert "@BotFather" in err
    assert "Traceback" not in err


# --- upgrading must not flood the chat --------------------------------------

def test_upgrading_from_a_capped_snapshot_resets_the_public_baseline(tmp_path):
    # 0.1.0 stored at most 200 scopes per public program. Diffing that against a
    # complete list would report every asset past 200 as newly added.
    from h1monitor.main import migrate_snapshots, SNAPSHOT_FORMAT
    from h1monitor.store import Store
    from h1monitor.models import Snapshot, Program
    from cryptography.fernet import Fernet

    st = Store(str(tmp_path / "m.db"), Fernet.generate_key())
    st.save_snapshot("public", Snapshot({"big": Program(
        "big", "Big", "open", True, None, None, {}, "d")}))
    st.save_snapshot("private", Snapshot({"acme": Program(
        "acme", "Acme", "open", True, None, None, {}, "d")}))

    assert migrate_snapshots(st) is True
    assert st.has_baseline("public") is False      # re-taken quietly next cycle
    assert st.has_baseline("private") is True      # private always paginated
    assert st.get_snapshot_format() == SNAPSHOT_FORMAT


def test_migration_runs_once_and_then_leaves_the_baseline_alone(tmp_path):
    from h1monitor.main import migrate_snapshots
    from h1monitor.store import Store
    from h1monitor.models import Snapshot
    from cryptography.fernet import Fernet

    st = Store(str(tmp_path / "m.db"), Fernet.generate_key())
    assert migrate_snapshots(st) is True
    st.save_snapshot("public", Snapshot({}))
    assert migrate_snapshots(st) is False          # no second reset
    assert st.has_baseline("public") is True
