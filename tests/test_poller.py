import pytest
from cryptography.fernet import Fernet

from h1monitor.store import Store
from h1monitor.models import Snapshot, Program
from h1monitor.poller import run_private_cycle
from h1monitor.directory_poller import run_public_cycle


class FakeNotifier:
    def __init__(self):
        self.changes = []
        self.texts = []

    async def send_changes(self, changes):
        self.changes.extend(changes)

    async def send_text(self, text):
        self.texts.append(text)


class FakePrivate:
    def __init__(self, snap):
        self._snap = snap

    async def fetch_private_snapshot(self, previous=None):
        return self._snap


class FakePublic:
    def __init__(self, snap):
        self._snap = snap

    async def fetch_public_snapshot(self):
        return self._snap


def _store(tmp_path):
    return Store(str(tmp_path / "p.db"), Fernet.generate_key())


def _prog(handle, state="open"):
    return Program(handle, handle.title(), state, True, "USD", "p", {})


@pytest.mark.asyncio
async def test_private_first_run_baseline_no_changes(tmp_path):
    st = _store(tmp_path)
    n = FakeNotifier()
    await run_private_cycle(st, FakePrivate(Snapshot({"acme": _prog("acme")})), n)
    assert n.changes == []
    assert any("Baseline" in t for t in n.texts)
    assert st.load_snapshot("private") is not None


@pytest.mark.asyncio
async def test_private_second_run_emits_state_change(tmp_path):
    st = _store(tmp_path)
    n = FakeNotifier()
    st.save_snapshot("private", Snapshot({"acme": _prog("acme", "open")}))
    await run_private_cycle(st, FakePrivate(Snapshot({"acme": _prog("acme", "paused")})), n)
    assert any("state" in c.summary for c in n.changes)


@pytest.mark.asyncio
async def test_public_first_run_silent(tmp_path):
    st = _store(tmp_path)
    n = FakeNotifier()
    await run_public_cycle(st, FakePublic(Snapshot({"a": _prog("a")})), n)
    assert n.changes == []
    assert st.has_baseline("public") is True


@pytest.mark.asyncio
async def test_public_second_run_new_program(tmp_path):
    st = _store(tmp_path)
    n = FakeNotifier()
    st.save_snapshot("public", Snapshot({"a": _prog("a")}))
    await run_public_cycle(
        st, FakePublic(Snapshot({"a": _prog("a"), "vercel": _prog("vercel")})), n
    )
    from h1monitor.models import ChangeType
    assert [c.program_handle for c in n.changes] == ["vercel"]
    assert all(ChangeType.NEW_PUBLIC_PROGRAM in c.types for c in n.changes)
