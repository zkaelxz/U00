"""Step 44: Discord / ntfy job notifications (services/notification_service,
api/routers/notification_routes). requests and DNS are faked: no network."""
import io
import os
import socket
import threading

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
requests = pytest.importorskip("requests")

from fastapi.testclient import TestClient

import background_jobs
import db
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service, notification_service as ns, settings_service
from translate_engines import redact_secrets

DISCORD = ("https://discord.com/api/webhooks/123456789012345678/"
           "SECRETtokenABCDEFGHIJKLMNOPQRSTUVWXYZ_-0123")
NTFY = "https://ntfy.sh/baihe-secret-topic-xyz"
LOCAL_NTFY = "http://192.168.1.50:8080/baihe-secret-topic-xyz"
SECRET_BITS = ("SECRETtoken", "baihe-secret-topic-xyz", "123456789012345678")
LOCAL = "http://127.0.0.1:8600"
REMOTE = "https://baihe.example.com"
PUBLIC_IP = "93.184.216.34"
_REAL_JOB_HOOK = background_jobs._notify_job_finished


@pytest.fixture
def env(tmp_path, monkeypatch, isolated_db):
    """Notifications on (conftest turns them off) with an empty .env. The
    background_jobs hook is a no-op here, so a job thread left running by an
    earlier test in this worker can't queue an event into this test; the
    tests that exercise the hook ask for `job_hook` as well."""
    path = tmp_path / ".env"
    monkeypatch.setattr(settings_service, "_default_env_path", lambda: str(path))
    for names in list(ns.ENV_VARS.values()) + [(ns.ALLOW_LOCAL_NTFY_ENV,), (ns.DISABLED_ENV,),
                                               (ns.API_PORT_ENV,)]:
        for n in names:
            monkeypatch.delenv(n, raising=False)
    monkeypatch.setattr(background_jobs, "_notify_job_finished", lambda *a, **k: None)
    ns.reset_for_tests()
    yield path
    ns.reset_for_tests()


@pytest.fixture
def job_hook(env, monkeypatch):
    """The real background_jobs -> notification_service hook."""
    monkeypatch.setattr(background_jobs, "_notify_job_finished", _REAL_JOB_HOOK)


@pytest.fixture
def dns(monkeypatch):
    """host -> list of addresses; unknown hosts fail to resolve."""
    table = {"discord.com": [PUBLIC_IP], "ntfy.sh": [PUBLIC_IP],
             "192.168.1.50": ["192.168.1.50"], "ntfy.lan": ["192.168.1.50"]}

    def fake(host, port, *a, **kw):
        if host not in table:
            raise socket.gaierror("no such host")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in table[host]]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    return table


class FakeResp:
    def __init__(self, status):
        self.status_code = status
        self.closed = False

    def close(self):
        self.closed = True

    def iter_content(self, *a, **k):
        raise AssertionError("the reply body must never be read")

    @property
    def content(self):
        raise AssertionError("the reply body must never be read")


@pytest.fixture
def posts(monkeypatch):
    """Records every Session.post; `.status` / `.exc` control the reply."""
    calls = []

    class Ctl:
        status = 204
        exc = None

    def fake_post(self, url, **kw):
        calls.append({"url": url, **kw})
        if Ctl.exc is not None:
            raise Ctl.exc
        resp = FakeResp(Ctl.status)
        calls[-1]["resp"] = resp
        return resp

    monkeypatch.setattr(requests.Session, "post", fake_post)
    Ctl.calls = calls
    return Ctl


@pytest.fixture
def no_timer(monkeypatch):
    started = []

    class T:
        def cancel(self):
            pass

    monkeypatch.setattr(ns, "_schedule_flush", lambda: started.append(1) or T())
    return started


def _write(env, **values):
    env.write_text("".join(f"{ns.ENV_VARS[k][0]}={v}\n" for k, v in values.items()))


def _log_text():
    p = os.path.join(db.LIBRARY_DIR, "logs", "app.log")
    return open(p, encoding="utf-8").read() if os.path.exists(p) else ""


def _no_secret(text):
    for bit in SECRET_BITS:
        assert bit not in text


def _client(key_writes=True, **kw):
    return TestClient(create_app(ApiSettings(allow_key_writes=key_writes, **kw)), base_url=LOCAL,
                      client=("127.0.0.1", 50000), raise_server_exceptions=False)


# --- URL rules ---------------------------------------------------------------

@pytest.mark.parametrize("url", [
    DISCORD, DISCORD.replace("discord.com", "discordapp.com"),
    DISCORD.replace("/api/", "/api/v10/"), DISCORD.replace("discord.com", "canary.discord.com")])
def test_discord_urls_accepted(env, url):
    assert ns.validate_url("discord", url) == url


@pytest.mark.parametrize("url", [
    DISCORD.replace("https", "http"),
    DISCORD.replace("discord.com", "discord.com.evil.example"),
    DISCORD.replace("discord.com", "evil.example"),
    DISCORD.replace("discord.com", "discord.com:8443"),
    DISCORD.replace("https://", "https://user:pw@"),
    DISCORD + "?wait=true",
    DISCORD + "#x",
    "https://discord.com/api/users/@me",
    "https://discord.com/api/webhooks/123/short",
    DISCORD + " extra", DISCORD + "\nBAIHE_X=1", "x" * 600, "",
])
def test_discord_urls_refused_without_echo(env, url):
    with pytest.raises(ns.InvalidInputError) as ei:
        ns.validate_url("discord", url)
    _no_secret(str(ei.value))


def test_ntfy_public_https_only_by_default(env):
    assert ns.validate_url("ntfy", NTFY) == NTFY
    for url in ("http://ntfy.sh/topic", "https://localhost/topic", "https://127.0.0.1/topic",
                "https://10.0.0.5/topic", "https://ntfy.local/topic", LOCAL_NTFY,
                "https://ntfy.sh/a/b", "https://ntfy.sh/topic?x=1", "https://ntfy.sh/"):
        with pytest.raises(ns.InvalidInputError):
            ns.validate_url("ntfy", url)


def test_ntfy_local_needs_explicit_opt_in(env, monkeypatch):
    with pytest.raises(ns.InvalidInputError, match="BAIHE_NTFY_ALLOW_LOCAL"):
        ns.validate_url("ntfy", LOCAL_NTFY)
    env.write_text(f"{ns.ALLOW_LOCAL_NTFY_ENV}=1\n")
    assert ns.allow_local_ntfy() is True
    assert ns.validate_url("ntfy", LOCAL_NTFY) == LOCAL_NTFY
    assert ns.validate_url("ntfy", "http://localhost:8080/t") == "http://localhost:8080/t"
    # IPv6 loopback is accepted too (it sits inside the reserved ::/8 block).
    assert ns.validate_url("ntfy", "http://[::1]:8080/t") == "http://[::1]:8080/t"
    # Link-local (cloud metadata) and other non-LAN ranges stay refused.
    for url in ("http://169.254.169.254/t", "http://0.0.0.0/t", "http://[fe80::1]/t",
                "http://224.0.0.1/t", "http://[::]/t", "http://[ff02::1]/t"):
        with pytest.raises(ns.InvalidInputError):
            ns.validate_url("ntfy", url)


def test_ipv6_loopback_is_refused_without_the_opt_in(env):
    with pytest.raises(ns.InvalidInputError, match="BAIHE_NTFY_ALLOW_LOCAL"):
        ns.validate_url("ntfy", "http://[::1]:8080/t")


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8501/t", "http://127.0.0.1:8600/t", "http://127.0.0.1:8756/t",
    "https://127.0.0.2:8756/t", "http://localhost:8600/t", "http://app.localhost:8501/t",
    "http://[::1]:8756/t", "http://[::ffff:127.0.0.1]:8756/t", "http://127.0.0.1:9123/t"])
def test_local_ntfy_never_targets_baihe_own_ports(env, monkeypatch, url):
    env.write_text(f"{ns.ALLOW_LOCAL_NTFY_ENV}=1\n")
    monkeypatch.setenv(ns.API_PORT_ENV, "9123")
    with pytest.raises(ns.InvalidInputError) as exc:
        ns.validate_url("ntfy", url)
    assert str(exc.value) == ns._NTFY_OWN_PORT   # fixed text, never the address


def test_api_port_from_dotenv_is_protected_too(env):
    env.write_text(f"{ns.ALLOW_LOCAL_NTFY_ENV}=1\n{ns.API_PORT_ENV}=9124\n")
    with pytest.raises(ns.InvalidInputError, match="belongs to Baihe"):
        ns.validate_url("ntfy", "http://127.0.0.1:9124/t")
    # A LAN address on those ports is not this PC's loopback and is allowed.
    assert ns.validate_url("ntfy", "http://192.168.1.50:8600/t")
    assert ns.validate_url("ntfy", "http://127.0.0.1:8080/t")


def test_unknown_channel(env):
    with pytest.raises(ns.InvalidInputError):
        ns.set_channel("email", NTFY)


# --- redaction ---------------------------------------------------------------

def test_redact_secrets_strips_discord_webhooks():
    for url in (DISCORD, DISCORD.replace("discord.com", "discordapp.com"),
                DISCORD.replace("/api/", "/api/v10/")):
        out = redact_secrets(f"POST {url} failed")
        _no_secret(out)
        assert "[REDACTED]" in out and out.endswith(" failed")


# --- message content ---------------------------------------------------------

def test_message_has_kind_title_outcome_only(env):
    did = db.create_drama(title_en="Moonlit Garden", title_zh="月光花园")
    assert ns.build_message(f"Translation (drama #{did})", "done") == \
        "Finished: Translation - Moonlit Garden"
    assert ns.build_message(f"Chunk & tag (drama {did})", "error") == \
        "Failed: Chunk & tag - Moonlit Garden"
    assert ns.build_message("Sources series (C:\\Users\\kae\\secret)", "done") == \
        "Finished: Sources series"
    assert ns.build_message(None, "error") == "Failed: Background job"
    assert ns.build_message("Translation (drama #99999)", "done") == "Finished: Translation"
    zh = db.create_drama(title_zh="月光花园")
    assert ns.build_message(f"Dub generation (drama #{zh})", "done") == \
        "Finished: Dub generation - 月光花园"
    assert "sk-" not in ns.build_message("Leak sk-ant-ABCDEFGHIJKLMNOP", "done")


def test_burst_summary():
    assert ns._summarize([("done", "a")]) == "a"
    assert ns._summarize([("done", "a"), ("error", "b"), ("done", "c")]) == \
        "3 background jobs ended: 2 finished, 1 failed."


# --- delivery ----------------------------------------------------------------

def test_discord_delivery_has_timeout_no_redirects_and_safe_payload(env, dns, posts):
    _write(env, discord=DISCORD)
    assert ns._deliver("Finished: Translation - @everyone") == {
        "discord": "sent", "ntfy": "not_configured"}
    (call,) = posts.calls
    assert call["url"] == DISCORD
    assert call["timeout"] == ns.HTTP_TIMEOUT
    assert call["allow_redirects"] is False
    import json
    body = json.loads(call["data"].decode("utf-8"))
    assert body["allowed_mentions"] == {"parse": []}
    assert body["content"] == "Baihe Subtitler: Finished: Translation - @everyone"


def test_ntfy_delivery(env, dns, posts):
    _write(env, ntfy=NTFY)
    assert ns._deliver("Failed: Dub generation") == {"discord": "not_configured", "ntfy": "sent"}
    (call,) = posts.calls
    assert call["data"] == b"Failed: Dub generation"
    assert call["headers"]["Title"] == "Baihe Subtitler"
    assert call["timeout"] == ns.HTTP_TIMEOUT


def test_connection_is_pinned_to_the_validated_address(env, dns, monkeypatch):
    """Through the real adapter: the socket goes to the resolved IP, not a
    second lookup of the host name."""
    seen = {}
    from requests.adapters import HTTPAdapter

    def fake_send(self, request, **kw):
        seen["url"] = request.url
        seen["host"] = request.headers.get("Host")
        seen["timeout"] = kw.get("timeout")
        r = requests.Response()
        r.status_code = 204
        r.raw = io.BytesIO(b"")        # stream=True: closed, never read
        return r

    monkeypatch.setattr(HTTPAdapter, "send", fake_send)
    _write(env, ntfy=NTFY)
    assert ns._deliver("x")["ntfy"] == "sent"
    assert seen["url"].startswith(f"https://{PUBLIC_IP}/")
    assert seen["host"] == "ntfy.sh"
    assert seen["timeout"] == ns.HTTP_TIMEOUT


def test_ssrf_refusals_never_connect(env, dns, posts):
    dns["ntfy.sh"] = ["10.1.2.3"]           # public name rebinding to a private address
    _write(env, ntfy=NTFY)
    assert ns._deliver("x")["ntfy"] == "refused"
    dns["ntfy.sh"] = [PUBLIC_IP, "127.0.0.1"]
    assert ns._deliver("x")["ntfy"] == "refused"
    # A hand-edited .env with a non-Discord "webhook" is refused at send time too.
    _write(env, discord="https://evil.example/api/webhooks/1/2")
    assert ns._deliver("x")["discord"] == "refused"
    dns["discord.com"] = ["169.254.169.254"]
    _write(env, discord=DISCORD)
    assert ns._deliver("x")["discord"] == "refused"
    assert posts.calls == []


def test_local_ntfy_only_when_opted_in(env, dns, posts):
    env.write_text(f"{ns.ENV_VARS['ntfy'][0]}={LOCAL_NTFY}\n")
    assert ns._deliver("x")["ntfy"] == "refused"
    env.write_text(f"{ns.ENV_VARS['ntfy'][0]}={LOCAL_NTFY}\n{ns.ALLOW_LOCAL_NTFY_ENV}=1\n")
    assert ns._deliver("x")["ntfy"] == "sent"
    # A LAN name that resolves to link-local is still refused.
    env.write_text(f"{ns.ENV_VARS['ntfy'][0]}=http://ntfy.lan/topic\n{ns.ALLOW_LOCAL_NTFY_ENV}=1\n")
    dns["ntfy.lan"] = ["169.254.169.254"]
    assert ns._deliver("x")["ntfy"] == "refused"
    assert len(posts.calls) == 1


def test_local_name_resolving_to_loopback_on_a_baihe_port_is_refused(env, dns, posts):
    dns["ntfy.lan"] = ["127.0.0.1"]
    for port in (8501, 8600, 8756):
        env.write_text(f"{ns.ENV_VARS['ntfy'][0]}=http://ntfy.lan:{port}/topic\n"
                       f"{ns.ALLOW_LOCAL_NTFY_ENV}=1\n")
        assert ns._deliver("x")["ntfy"] == "refused"
    dns["ntfy.lan"] = ["::1"]
    assert ns._deliver("x")["ntfy"] == "refused"
    assert posts.calls == []
    env.write_text(f"{ns.ENV_VARS['ntfy'][0]}=http://ntfy.lan:8080/topic\n"
                   f"{ns.ALLOW_LOCAL_NTFY_ENV}=1\n")
    assert ns._deliver("x")["ntfy"] == "sent"
    assert posts.calls[0]["url"] == "http://ntfy.lan:8080/topic"


def test_reply_is_streamed_and_its_body_never_read(env, dns, posts):
    _write(env, ntfy=NTFY)
    assert ns._deliver("x")["ntfy"] == "sent"
    call = posts.calls[0]
    assert call["stream"] is True and call["allow_redirects"] is False
    assert call["resp"].closed


def _serve_once(handler):
    """One-connection loopback server running `handler(conn)` in a thread."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    stop = threading.Event()

    def run():
        try:
            conn, _ = srv.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(10)
            try:
                conn.recv(65536)
                handler(conn, stop)
            except OSError:
                pass

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return srv, stop, t


def test_a_slow_dripping_server_is_cut_off_by_the_deadline(monkeypatch):
    import time

    def drip(conn, stop):
        conn.sendall(b"HTTP/1.1 200 OK\r\n")
        while not stop.is_set():       # headers never finish; each byte beats HTTP_TIMEOUT
            conn.sendall(b"X")
            time.sleep(0.1)

    monkeypatch.setattr(ns, "SEND_DEADLINE", 0.6)
    srv, stop, t = _serve_once(drip)
    port = srv.getsockname()[1]
    started = time.monotonic()
    try:
        with pytest.raises(Exception):
            ns._pinned_post(f"http://slow.test:{port}/t", "127.0.0.1", b"x", {})
        assert time.monotonic() - started < 4
    finally:
        stop.set()
        srv.close()
        t.join(5)


def test_a_huge_reply_body_is_not_downloaded():
    import time

    sent = []

    def huge(conn, stop):
        conn.sendall(b"HTTP/1.1 204 No Content\r\nContent-Length: 10000000000\r\n\r\n")
        chunk = b"0" * 65536
        while not stop.is_set():
            try:
                conn.sendall(chunk)
            except OSError:
                return
            sent.append(len(chunk))

    srv, stop, t = _serve_once(huge)
    port = srv.getsockname()[1]
    started = time.monotonic()
    try:
        assert ns._pinned_post(f"http://big.test:{port}/t", "127.0.0.1", b"x", {}) == 204
        assert time.monotonic() - started < 3
    finally:
        stop.set()
        srv.close()
        t.join(5)
    assert sum(sent) < 100_000_000


def test_failures_are_reported_and_logged_without_secrets(env, dns, posts):
    _write(env, discord=DISCORD, ntfy=NTFY)
    posts.status = 500
    assert ns._deliver("x") == {"discord": "failed", "ntfy": "failed"}
    posts.exc = requests.ConnectionError(f"could not reach {DISCORD} / {NTFY}")
    assert ns._deliver("x") == {"discord": "failed", "ntfy": "failed"}
    posts.exc = requests.Timeout(f"timed out {NTFY}")
    assert ns._deliver("x") == {"discord": "failed", "ntfy": "failed"}
    del dns["ntfy.sh"]
    posts.exc = None
    assert ns._deliver("x")["ntfy"] == "failed"
    log = _log_text()
    assert "HTTP 500" in log and "ConnectionError" in log
    _no_secret(log)


# --- rate limit and burst collapse ------------------------------------------

def test_burst_collapses_into_one_message(env, dns, posts, no_timer):
    _write(env, discord=DISCORD)
    ns.notify_job_finished("Translation (drama #1)", "done")
    ns.notify_job_finished("Dub generation (drama #2)", "error")
    ns.notify_job_finished("Library backup", "done")
    ns.notify_job_finished("Cancelled thing", "cancelled")   # not an ending we push
    assert len(no_timer) == 1          # one timer for the whole burst
    assert posts.calls == []           # nothing sent from the job thread
    assert ns.flush() == {"discord": "sent", "ntfy": "not_configured"}
    import json
    assert json.loads(posts.calls[0]["data"])["content"].endswith(
        "3 background jobs ended: 2 finished, 1 failed.")
    assert ns.flush() == {}


def test_per_minute_cap(env, dns, posts, no_timer):
    _write(env, discord=DISCORD)
    for _ in range(ns.MAX_PER_MINUTE):
        ns.notify_job_finished("Translation", "done")
        assert ns.flush()["discord"] == "sent"
    ns.notify_job_finished("Translation", "done")
    assert ns.flush() == {}
    assert len(posts.calls) == ns.MAX_PER_MINUTE
    assert "rate limit" in _log_text()


def test_slots_free_up_after_a_minute():
    ns.reset_for_tests()
    try:
        for i in range(ns.MAX_PER_MINUTE):
            assert ns._take_slot_locked(now=100.0 + i)
        assert not ns._take_slot_locked(now=120.0)
        assert ns._take_slot_locked(now=161.0)
    finally:
        ns.reset_for_tests()


def test_nothing_queued_without_a_channel_or_when_disabled(env, posts, no_timer, monkeypatch):
    ns.notify_job_finished("Translation", "done")
    assert no_timer == []
    _write(env, discord=DISCORD)
    monkeypatch.setenv(ns.DISABLED_ENV, "1")
    ns.notify_job_finished("Translation", "done")
    assert no_timer == [] and ns._pending == []


def test_conftest_disables_automatic_sends():
    assert os.environ.get(ns.DISABLED_ENV) == "1"


# --- background_jobs hook ----------------------------------------------------

def _wait(job_id):
    import time
    for _ in range(200):
        job = background_jobs.get_status(job_id)
        if job and job["status"] in ("done", "error", "cancelled"):
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_job_end_queues_a_push_and_a_failed_send_never_breaks_the_job(env, job_hook, dns, posts,
                                                                      no_timer, monkeypatch):
    _write(env, discord=DISCORD)
    posts.exc = requests.ConnectionError(DISCORD)
    # The runner flips a job's status first and queues the push after, so
    # polling the status (or _pending) races the queueing. Wait instead for
    # notify_job_finished itself to return for both of this test's jobs; a
    # job thread left over from an earlier test in this worker may also
    # queue an event while the env fixture has notifications on, so only
    # this test's own messages are checked.
    ours = {"Translation (drama #1)", "Dub generation (drama #1)"}
    seen, both_queued = [], threading.Event()
    real = ns.notify_job_finished

    def recording(description, status):
        real(description, status)
        if description in ours:
            seen.append((description, status))
            if len(seen) == len(ours):
                both_queued.set()

    monkeypatch.setattr(ns, "notify_job_finished", recording)

    def boom():
        raise RuntimeError("real job error")

    background_jobs.start_job("notify_t1", lambda: None, description="Translation (drama #1)")
    background_jobs.start_job("notify_t2", boom, description="Dub generation (drama #1)")
    assert both_queued.wait(timeout=30), f"only {seen} were queued"
    assert background_jobs.get_status("notify_t1")["status"] == "done"
    job = background_jobs.get_status("notify_t2")
    assert job["status"] == "error" and "real job error" in job["error"]
    queued = [(status, msg) for status, msg in ns._pending
              if msg in ("Finished: Translation", "Failed: Dub generation")]
    assert sorted(queued) == [("done", "Finished: Translation"),
                              ("error", "Failed: Dub generation")]
    assert len(no_timer) == 1
    assert ns.flush() == {"discord": "failed", "ntfy": "not_configured"}


def test_a_raising_notifier_never_breaks_the_job(env, job_hook, monkeypatch):
    def explode(*a, **k):
        raise RuntimeError("notifier down")

    monkeypatch.setattr(ns, "notify_job_finished", explode)
    background_jobs.start_job("notify_t3", lambda: None, description="Translation")
    assert _wait("notify_t3")["status"] == "done"


def test_flush_never_raises(env, monkeypatch, no_timer):
    _write(env, discord=DISCORD)
    ns.notify_job_finished("Translation", "done")
    monkeypatch.setattr(ns, "_deliver", lambda text: (_ for _ in ()).throw(RuntimeError("x")))
    assert ns.flush() == {}


# --- API ---------------------------------------------------------------------

def test_status_is_booleans_only(env):
    _write(env, discord=DISCORD, ntfy=NTFY)
    r = _client().get("/api/settings/notifications")
    assert r.status_code == 200
    assert r.json() == {"discord_configured": True, "ntfy_configured": True,
                        "ntfy_allow_local": False}
    _no_secret(r.text)


def test_set_and_clear_round_trip(env):
    c = _client()
    r = c.post("/api/settings/notifications/discord", json={"value": DISCORD, "confirm": True})
    assert r.status_code == 200 and r.json() == {"channel": "discord", "configured": True}
    _no_secret(r.text)
    assert settings_service.resolve_env_names(ns.ENV_VARS["discord"]) == DISCORD
    r = c.post("/api/settings/notifications/ntfy", json={"value": NTFY, "confirm": True})
    assert r.json() == {"channel": "ntfy", "configured": True}
    st = c.get("/api/settings/notifications").json()
    assert st["discord_configured"] and st["ntfy_configured"]
    r = c.post("/api/settings/notifications/discord/clear", json={"confirm": True})
    assert r.json() == {"channel": "discord", "configured": False}
    assert f"{ns.ENV_VARS['ntfy'][0]}={NTFY}" in env.read_text()
    assert "DISCORD" not in env.read_text()
    # Not listed among engine keys (no leak into the general overview either).
    ov = c.get("/api/settings")
    assert "discord" not in ov.json()["engine_keys"]
    _no_secret(ov.text + _log_text())


def test_set_errors(env):
    c = _client()
    bad = DISCORD.replace("discord.com", "evil.example")
    r = c.post("/api/settings/notifications/discord", json={"value": bad, "confirm": True})
    assert r.status_code == 422
    _no_secret(r.text)
    assert "evil" not in r.text
    r = c.post("/api/settings/notifications/discord", json={"value": DISCORD})
    assert r.status_code == 422 and not env.exists()
    r = c.post("/api/settings/notifications/email", json={"value": DISCORD, "confirm": True})
    assert r.status_code == 422
    _no_secret(r.text)
    r = c.post("/api/settings/notifications/discord", json={"value": DISCORD, "confirm": True,
                                                            "extra": 1})
    assert r.status_code == 422
    _no_secret(r.text)
    r = c.post("/api/settings/notifications/discord/clear", json={})
    assert r.status_code == 422


def test_set_and_clear_use_the_key_write_gate(env):
    c = _client(key_writes=False)
    assert c.post("/api/settings/notifications/discord",
                  json={"value": DISCORD, "confirm": True}).status_code == 403
    assert c.post("/api/settings/notifications/discord/clear",
                  json={"confirm": True}).status_code == 403
    assert not env.exists()
    c = _client()
    for headers in ({"X-Forwarded-For": "1.2.3.4"}, {"Origin": "https://evil.example"}):
        assert c.post("/api/settings/notifications/discord",
                      json={"value": DISCORD, "confirm": True}, headers=headers).status_code == 403
    assert not env.exists()


def test_send_test_route(env, dns, posts):
    c = _client()
    r = c.post("/api/settings/notifications/test", json={})
    assert r.status_code == 422      # nothing configured
    _write(env, discord=DISCORD, ntfy=NTFY)
    r = c.post("/api/settings/notifications/test", json={})
    assert r.status_code == 200
    assert r.json() == {"results": {"discord": "sent", "ntfy": "sent"}}
    _no_secret(r.text)
    posts.exc = requests.ConnectionError(f"{DISCORD} {NTFY}")
    r = c.post("/api/settings/notifications/test", json={})
    assert r.json() == {"results": {"discord": "failed", "ntfy": "failed"}}
    _no_secret(r.text + _log_text())


def test_send_test_is_rate_limited(env, dns, posts):
    _write(env, discord=DISCORD)
    c = _client()
    for _ in range(ns.MAX_PER_MINUTE):
        assert c.post("/api/settings/notifications/test", json={}).status_code == 200
    r = c.post("/api/settings/notifications/test", json={})
    assert r.status_code == 429
    assert len(posts.calls) == ns.MAX_PER_MINUTE


def test_simple_cross_site_post_refused(env, dns, posts):
    _write(env, discord=DISCORD)
    r = _client().post("/api/settings/notifications/test", content=b"x",
                       headers={"Content-Type": "text/plain"})
    assert r.status_code == 403 and posts.calls == []


# --- auth on ------------------------------------------------------------------

def _session(admin=False, *perms):
    if admin:
        u = auth_service.grant_admin_local("admin@example.com")
    else:
        u = auth_service.add_user("kid@example.com")
        for p in perms:
            auth_service.grant_permission(u["id"], p)
    return auth_service.create_session(u["id"], "pytest", "203.0.113.9")


def _h(s):
    return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
            api_auth.CSRF_HEADER: s["csrf_token"]}


def test_auth_on_status_needs_admin_settings(env):
    _write(env, discord=DISCORD)
    c = TestClient(create_app(ApiSettings(auth_mode="on", allow_key_writes=True)),
                   base_url=REMOTE, raise_server_exceptions=False)
    assert c.get("/api/settings/notifications").status_code == 401
    kid = _session(False, "library.read", "jobs.start")
    assert c.get("/api/settings/notifications", headers=_h(kid)).status_code == 403
    admin = _session(True)
    r = c.get("/api/settings/notifications", headers=_h(admin))
    assert r.status_code == 200 and r.json()["discord_configured"] is True
    _no_secret(r.text)


def test_auth_on_writes_and_test_are_pc_only_even_for_admin(env, dns, posts):
    app = create_app(ApiSettings(auth_mode="on", allow_key_writes=True))
    remote = TestClient(app, base_url=REMOTE, raise_server_exceptions=False)
    admin = _session(True)
    for path, body in (("/api/settings/notifications/discord", {"value": DISCORD,
                                                                "confirm": True}),
                       ("/api/settings/notifications/discord/clear", {"confirm": True}),
                       ("/api/settings/notifications/test", {})):
        assert remote.post(path, json=body).status_code in (401, 403)
        assert remote.post(path, json=body, headers=_h(admin)).status_code == 403
    assert not env.exists() and posts.calls == []
    local = TestClient(app, base_url=LOCAL, client=("127.0.0.1", 50000),
                       raise_server_exceptions=False)
    r = local.post("/api/settings/notifications/discord", json={"value": DISCORD, "confirm": True})
    assert r.status_code == 200 and r.json()["configured"] is True
    r = local.post("/api/settings/notifications/test", json={})
    assert r.status_code == 200 and r.json()["results"]["discord"] == "sent"
