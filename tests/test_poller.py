import pytest
from cryptography.fernet import Fernet

from h1monitor.store import Store
from h1monitor.models import Snapshot, Program, DirectoryProgram
from h1monitor.poller import run_api_cycle
from h1monitor.directory_poller import run_directory_cycle


class FakeNotifier:
    def __init__(self):
        self.changes = []
        self.texts = []

    async def send_changes(self, changes):
        self.changes.extend(changes)

    async def send_text(self, text):
        self.texts.append(text)


class FakeH1:
    def __init__(self, snap):
        self._snap = snap
        self.scope_handles = "unset"

    async def fetch_snapshot(self, previous=None, scope_handles=None):
        self.scope_handles = scope_handles
        return self._snap


class FakeDir:
    def __init__(self, progs):
        self._progs = progs

    async def fetch_all(self):
        return self._progs


def _store(tmp_path):
    return Store(str(tmp_path / "p.db"), Fernet.generate_key())


@pytest.mark.asyncio
async def test_api_first_run_baseline_no_changes(tmp_path):
    st = _store(tmp_path)
    n = FakeNotifier()
    snap = Snapshot({"acme": Program("acme", "Acme", "open", True, "USD", "p", {})})
    await run_api_cycle(st, FakeH1(snap), n)
    assert n.changes == []
    assert any("Baseline" in t for t in n.texts)
    assert st.load_api_snapshot() is not None


@pytest.mark.asyncio
async def test_api_second_run_emits_changes(tmp_path):
    st = _store(tmp_path)
    n = FakeNotifier()
    st.save_api_snapshot(
        Snapshot({"acme": Program("acme", "Acme", "open", True, "USD", "p", {})})
    )
    snap = Snapshot({"acme": Program("acme", "Acme", "paused", True, "USD", "p", {})})
    await run_api_cycle(st, FakeH1(snap), n)
    assert any("state" in c.summary for c in n.changes)


@pytest.mark.asyncio
async def test_api_cycle_passes_allowlist_as_scope_handles(tmp_path):
    from h1monitor.models import Preferences
    st = _store(tmp_path)
    prefs = Preferences.defaults()
    prefs.allowlist = frozenset({"acme", "beta"})
    st.save_preferences(prefs)
    st.save_api_snapshot(Snapshot({"acme": Program("acme", "Acme", "open", True, "USD", "p", {})}))
    fake = FakeH1(Snapshot({"acme": Program("acme", "Acme", "open", True, "USD", "p", {})}))
    await run_api_cycle(st, fake, FakeNotifier())
    assert fake.scope_handles == {"acme", "beta"}


@pytest.mark.asyncio
async def test_directory_first_run_silent(tmp_path):
    st = _store(tmp_path)
    n = FakeNotifier()
    await run_directory_cycle(
        st, FakeDir([DirectoryProgram("a", "A", True, "open", "d", "u")]), n
    )
    assert n.changes == []
    assert st.has_directory_baseline() is True


@pytest.mark.asyncio
async def test_directory_second_run_new_program(tmp_path):
    st = _store(tmp_path)
    n = FakeNotifier()
    st.save_directory_handles({"a"})
    await run_directory_cycle(
        st,
        FakeDir(
            [
                DirectoryProgram("a", "A", True, "open", "d", "u"),
                DirectoryProgram("vercel", "Vercel", True, "open", "2026-08-18", "u"),
            ]
        ),
        n,
    )
    assert [c.program_handle for c in n.changes] == ["vercel"]
