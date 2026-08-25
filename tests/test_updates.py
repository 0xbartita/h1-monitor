import httpx
import pytest

from h1monitor.updates import (
    UpdateClient, is_newer, update_notice, channel_link,
)


# --- version comparison -----------------------------------------------------

def test_is_newer_compares_release_tags():
    assert is_newer("v0.2.0", "0.1.0") is True
    assert is_newer("0.2.0", "v0.1.0") is True      # the leading v is noise
    assert is_newer("v0.1.0", "0.1.0") is False     # same version
    assert is_newer("v0.1.0", "0.2.0") is False     # older upstream, e.g. a rollback
    assert is_newer("v1.0.0", "0.9.9") is True      # not string comparison
    assert is_newer("v0.10.0", "0.9.0") is True     # 10 > 9, not "10" < "9"


def test_is_newer_is_safe_on_junk_tags():
    """A tag we can't parse must never claim an update — the notice links people
    at an upgrade they may not need."""
    assert is_newer("nightly", "0.1.0") is False
    assert is_newer("", "0.1.0") is False
    assert is_newer(None, "0.1.0") is False


# --- fetching the latest release -------------------------------------------

def _client(handler):
    return UpdateClient("owner/repo", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_latest_release_is_read_from_the_releases_endpoint():
    def handler(request):
        assert request.url.path.endswith("/releases/latest")
        return httpx.Response(200, json={
            "tag_name": "v0.3.0",
            "html_url": "https://github.com/owner/repo/releases/tag/v0.3.0",
        })

    got = await _client(handler).latest_release()
    assert got == ("v0.3.0", "https://github.com/owner/repo/releases/tag/v0.3.0")


@pytest.mark.asyncio
async def test_falls_back_to_tags_when_no_release_is_published():
    """`git push --tags` alone builds an image but publishes no Release, so a
    repo can legitimately have tags and no releases."""
    def handler(request):
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(200, json=[{"name": "v0.2.0"}, {"name": "v0.1.0"}])

    tag, url = await _client(handler).latest_release()
    assert tag == "v0.2.0"
    assert url.endswith("/releases/tag/v0.2.0")


@pytest.mark.asyncio
async def test_update_check_failure_is_swallowed():
    """GitHub being down, rate-limiting, or offline must never break the bot."""
    def handler(request):
        raise httpx.ConnectError("no network")

    assert await _client(handler).latest_release() is None


@pytest.mark.asyncio
async def test_garbage_json_does_not_raise():
    def handler(request):
        return httpx.Response(200, json={"unexpected": "shape"})

    assert await _client(handler).latest_release() is None


# --- what the update message points at -------------------------------------

def test_notice_links_the_release_page_and_the_channel():
    """The bot no longer guesses how you installed it — the release notes carry
    the upgrade steps, and the channel carries the news."""
    notice = update_notice("0.1.0", "v0.2.0", "https://example.com/rel")
    assert 'href="https://example.com/rel"' in notice     # clickable release
    assert "0.1.0" in notice and "0.2.0" in notice
    assert 'href="https://t.me/h1_monitor"' in notice     # clickable channel


def test_notice_embeds_no_install_commands():
    """Install-specific commands were guesswork and could send someone down the
    wrong path; the release notes say it properly."""
    notice = update_notice("0.1.0", "v0.2.0", "https://example.com/rel")
    for cmd in ("docker pull", "docker rm", "systemctl", "install.sh"):
        assert cmd not in notice


def test_channel_link_is_a_real_anchor():
    assert channel_link() == '<a href="https://t.me/h1_monitor">@h1_monitor</a>'


# --- remembering what we've already announced -------------------------------

def _store(tmp_path):
    from cryptography.fernet import Fernet
    from h1monitor.store import Store
    return Store(str(tmp_path / "u.db"), Fernet.generate_key())


def test_store_round_trips_the_known_release(tmp_path):
    st = _store(tmp_path)
    assert st.get_known_release() is None
    st.set_known_release("v0.2.0", "https://example.com/rel")
    assert st.get_known_release() == ("v0.2.0", "https://example.com/rel")


@pytest.mark.asyncio
async def test_update_loop_announces_a_new_version_once(tmp_path):
    """Users who never open /status still need to hear about a release — but
    exactly once, not every check for the rest of the version's life."""
    import asyncio
    from h1monitor.updates import update_check_loop

    class N:
        def __init__(self): self.texts = []
        async def send_text(self, t): self.texts.append(t); return True
        async def send_changes(self, c): pass

    def handler(request):
        return httpx.Response(200, json={
            "tag_name": "v9.9.9",
            "html_url": "https://github.com/owner/repo/releases/tag/v9.9.9",
        })

    st, n = _store(tmp_path), N()
    stop, wake = asyncio.Event(), asyncio.Event()
    task = asyncio.ensure_future(update_check_loop(
        st, lambda: _client(handler), n, stop, wake, interval_seconds=0.05,
    ))
    await asyncio.sleep(0.05)
    wake.set()                       # force a second check straight away
    await asyncio.sleep(0.15)
    stop.set()
    await asyncio.wait_for(task, timeout=3)

    notices = [t for t in n.texts if "Update available" in t]
    assert len(notices) == 1, f"announced {len(notices)} times, expected once"
    assert "9.9.9" in notices[0]
    assert st.get_known_release()[0] == "v9.9.9"


@pytest.mark.asyncio
async def test_update_loop_stays_silent_when_current(tmp_path):
    import asyncio
    from h1monitor.updates import update_check_loop

    class N:
        def __init__(self): self.texts = []
        async def send_text(self, t): self.texts.append(t); return True
        async def send_changes(self, c): pass

    def handler(request):
        return httpx.Response(200, json={
            "tag_name": "v0.0.1",           # older than us
            "html_url": "https://example.com/old",
        })

    st, n = _store(tmp_path), N()
    stop = asyncio.Event()
    task = asyncio.ensure_future(update_check_loop(
        st, lambda: _client(handler), n, stop, None, interval_seconds=0.05,
    ))
    await asyncio.sleep(0.1)
    stop.set()
    await asyncio.wait_for(task, timeout=3)
    assert n.texts == []


@pytest.mark.asyncio
async def test_update_loop_survives_a_dead_github(tmp_path):
    import asyncio
    from h1monitor.updates import update_check_loop

    class N:
        async def send_text(self, t): return True
        async def send_changes(self, c): pass

    def handler(request):
        raise httpx.ConnectError("no network")

    st = _store(tmp_path)
    stop = asyncio.Event()
    task = asyncio.ensure_future(update_check_loop(
        st, lambda: _client(handler), N(), stop, None, interval_seconds=0.05,
    ))
    await asyncio.sleep(0.1)
    stop.set()
    await asyncio.wait_for(task, timeout=3)   # must not have raised


# --- two-part version tags (1.0, 2.0) ---------------------------------------

def test_a_two_part_tag_is_a_real_version():
    # Tagging v1.0 must not read as "unparseable", which is silently treated as
    # "no update available" — forever, for everyone on an older build.
    assert is_newer("v1.0", "0.9.0") is True
    assert is_newer("v2.0", "1.0") is True
    assert is_newer("v1.0", "1.0") is False


def test_two_and_three_part_tags_compare_against_each_other():
    assert is_newer("v1.0", "0.2.0") is True      # 1.0 beats 0.2.0
    assert is_newer("v1.0.1", "1.0") is True      # a patch on top of 1.0
    assert is_newer("v1.0", "1.0.1") is False     # and not the other way
    assert is_newer("0.10.0", "0.9.0") is True    # still numeric, not text


def test_still_refuses_things_that_are_not_versions():
    for junk in ("nightly", "", "v", "latest", None):
        assert is_newer(junk, "0.1.0") is False
