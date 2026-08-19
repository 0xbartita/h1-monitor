from h1monitor.differ import diff_directory
from h1monitor.models import DirectoryProgram, ChangeType


def _dp(handle, state="open"):
    return DirectoryProgram(
        handle, handle.title(), True, state, "2026-08-18",
        f"https://hackerone.com/{handle}",
    )


def test_first_run_silent():
    assert diff_directory(set(), [_dp("a"), _dp("b")], first_run=True) == []


def test_new_program_detected():
    changes = diff_directory({"a"}, [_dp("a"), _dp("vercel")], first_run=False)
    assert len(changes) == 1
    c = changes[0]
    assert ChangeType.NEW_PUBLIC_PROGRAM in c.types
    assert c.program_handle == "vercel"
    assert c.directory.started_accepting_at == "2026-08-18"


def test_no_new_programs():
    assert diff_directory({"a", "b"}, [_dp("a"), _dp("b")], first_run=False) == []
