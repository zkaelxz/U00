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
from jobs import job_launch, job_runner, job_settings, job_table
import env_reader
import db
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service, jobs_service, notification_service as ns, settings_service
from engine_backends import paid_calls, run_limits
from engine_backends.shared import redact_secrets
from tests.api_helpers import session_headers
from tests.job_helpers import wait_for_job

DISCORD = ("https://discord.com/api/webhooks/123456789012345678/"
           "SECRETtokenABCDEFGHIJKLMNOPQRSTUVWXYZ_-0123")
NTFY = "https://ntfy.sh/baihe-secret-topic-xyz"
LOCAL_NTFY = "http://192.168.1.50:8080/baihe-secret-topic-xyz"
SECRET_BITS = ("SECRETtoken", "baihe-secret-topic-xyz", "123456789012345678")
LOCAL = "http://127.0.0.1:8600"
REMOTE = "https://baihe.example.com"
PUBLIC_IP = "93.184.216.34"
_REAL_JOB_HOOK = job_settings._notify_job_finished


@pytest.fixture
def env(tmp_path, monkeypatch, isolated_db):
    """Notifications on (conftest turns them off) with an empty .env. The
    background_jobs hook is a no-op here, so a job thread left running by an
    earlier test in this worker can't queue an event into this test; the
    tests that exercise the hook ask for `job_hook` as well."""
    path = tmp_path / ".env"
    monkeypatch.setattr(env_reader, "default_env_path", lambda: str(path))
    for names in list(ns.ENV_VARS.values()) + [(ns.ALLOW_LOCAL_NTFY_ENV,), (ns.DISABLED_ENV,),
                                               (settings_service.API_PORT_ENV,), (settings_service.HOUSEHOLD_PORT_ENV,)]:
        for n in names:
            monkeypatch.delenv(n, raising=False)
    monkeypatch.setattr(job_runner, "_notify_job_finished", lambda *a, **k: None)
    ns.reset_for_tests()
    yield path
    ns.reset_for_tests()


@pytest.fixture
def job_hook(env, monkeypatch):
    """The real background_jobs -> notification_service hook."""
    monkeypatch.setattr(job_runner, "_notify_job_finished", _REAL_JOB_HOOK)


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

    monkeypatch.setattr(ns, "_schedule_flush",
                        lambda delay=ns.BURST_WINDOW: started.append(delay) or T())
    return started


def _discord_text(call) -> str:
    """What a Discord post says, whether it is plain content or a card."""
    import json
    body = json.loads(call["data"].decode("utf-8"))
    if "content" in body:
        return body["content"]
    card = body["embeds"][0]
    return "\n".join(x for x in (card.get("title"), card.get("description")) if x)


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
    "http://127.0.0.1:8600/t", "http://127.0.0.1:8756/t",
    "https://127.0.0.2:8756/t", "http://localhost:8600/t", "http://app.localhost:8600/t",
    "http://[::1]:8756/t", "http://[::ffff:127.0.0.1]:8756/t", "http://127.0.0.1:9123/t"])
def test_local_ntfy_never_targets_baihe_own_ports(env, monkeypatch, url):
    env.write_text(f"{ns.ALLOW_LOCAL_NTFY_ENV}=1\n")
    monkeypatch.setenv(settings_service.API_PORT_ENV, "9123")
    with pytest.raises(ns.InvalidInputError) as exc:
        ns.validate_url("ntfy", url)
    assert str(exc.value) == ns._NTFY_OWN_PORT   # fixed text, never the address


def test_api_port_from_dotenv_is_protected_too(env):
    env.write_text(f"{ns.ALLOW_LOCAL_NTFY_ENV}=1\n{settings_service.API_PORT_ENV}=9124\n")
    with pytest.raises(ns.InvalidInputError, match="belongs to Baihe"):
        ns.validate_url("ntfy", "http://127.0.0.1:9124/t")
    # A LAN address on those ports is not this PC's loopback and is allowed.
    assert ns.validate_url("ntfy", "http://192.168.1.50:8600/t")
    assert ns.validate_url("ntfy", "http://127.0.0.1:8080/t")


def test_household_port_is_protected_only_when_set(env, monkeypatch):
    env.write_text(f"{ns.ALLOW_LOCAL_NTFY_ENV}=1\n")
    assert ns.validate_url("ntfy", "http://127.0.0.1:8610/t")   # unset: an ordinary port
    monkeypatch.setenv(settings_service.HOUSEHOLD_PORT_ENV, "8610")
    with pytest.raises(ns.InvalidInputError, match="belongs to Baihe"):
        ns.validate_url("ntfy", "http://127.0.0.1:8610/t")
    monkeypatch.delenv(settings_service.HOUSEHOLD_PORT_ENV)
    env.write_text(f"{ns.ALLOW_LOCAL_NTFY_ENV}=1\n{settings_service.HOUSEHOLD_PORT_ENV}=8611\n")
    with pytest.raises(ns.InvalidInputError, match="belongs to Baihe"):
        ns.validate_url("ntfy", "http://127.0.0.1:8611/t")


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


def test_redact_secrets_masks_url_userinfo():
    for scheme in ("http", "https", "ftp"):
        out = redact_secrets(f"GET {scheme}://bob:p%40ss:w0rd!@proxy.example:8080/a/b failed")
        assert out == f"GET {scheme}://***@proxy.example:8080/a/b failed"
    assert redact_secrets("https://ghtoken123@example.com/x") == "https://***@example.com/x"
    assert redact_secrets("http://u:p@ss@host/p") == "http://***@host/p"
    assert redact_secrets("see https://example.com/a@b") == "see https://example.com/a@b"


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
    assert ns.build_message(f"Translation (drama #{did})", "done", with_errors=True).startswith(
        "Finished with errors: Translation")
    assert ns.build_message("Translation (drama #99999)", "done") == "Finished: Translation"
    zh = db.create_drama(title_zh="月光花园")
    assert ns.build_message(f"Dub generation (drama #{zh})", "done") == \
        "Finished: Dub generation - 月光花园"
    assert "sk-" not in ns.build_message("Leak sk-ant-ABCDEFGHIJKLMNOP", "done")


def test_burst_summary():
    assert ns._summarize([("done", "a")]) == "a"
    assert ns._summarize([("done", "a"), ("done", "c")]) == "2 background jobs finished."
    assert ns._summarize([("done", "a"), ("error", "Failed: b: no key"), ("done", "c")]) == \
        "2 background jobs finished. Failed: b: no key."


# --- delivery ----------------------------------------------------------------

def test_discord_delivery_has_timeout_no_redirects_and_safe_payload(env, dns, posts, monkeypatch):
    monkeypatch.setattr(ns.socket, "gethostname", lambda: "DESKTOP-EXAMPLE")
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
    assert body["content"] == "Baihe Studio (DESKTOP-EXAMPLE): Finished: Translation - @everyone"


def test_discord_card_footer_names_the_pc_and_no_name_leaves_it_out(monkeypatch):
    import json
    card = {"title": "Finished \u00b7 Translation", "color": ns.COLOR_OK}
    monkeypatch.setattr(ns.socket, "gethostname", lambda: "V100-BOX")
    body = json.loads(ns._payload("discord", "x", card)[0])
    assert body["embeds"][0]["footer"] == {"text": "Sent from V100-BOX"}
    assert "footer" not in card
    assert ns._payload("ntfy", "x")[0] == b"x"

    def boom():
        raise OSError
    monkeypatch.setattr(ns.socket, "gethostname", boom)
    body = json.loads(ns._payload("discord", "x", card)[0])
    assert "footer" not in body["embeds"][0]
    assert json.loads(ns._payload("discord", "x")[0])["content"] == "Baihe Studio: x"


def test_ntfy_delivery(env, dns, posts):
    _write(env, ntfy=NTFY)
    assert ns._deliver("Failed: Dub generation") == {"discord": "not_configured", "ntfy": "sent"}
    (call,) = posts.calls
    assert call["data"] == b"Failed: Dub generation"
    assert call["headers"]["Title"] == "Baihe Studio"
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
    for port in (8600, 8756):
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
        with pytest.raises(TimeoutError, match="send deadline passed"):
            ns._pinned_post(f"http://slow.test:{port}/t", "127.0.0.1", b"x", {})
        elapsed = time.monotonic() - started
        assert ns.SEND_DEADLINE * 0.8 <= elapsed < 4
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
    ns.notify_job_finished("Translation (title #1)", "done")
    ns.notify_job_finished("Dub generation (drama #2)", "error")
    ns.notify_job_finished("Library backup", "done")
    ns.notify_job_finished("Cancelled thing", "cancelled")   # not an ending we push
    # The first success waits for repeats; the failure cuts the wait to the burst window.
    assert no_timer == [ns.GROUP_WINDOW, ns.BURST_WINDOW]
    assert posts.calls == []           # nothing sent from the job thread
    assert ns.flush() == {"discord": "sent", "ntfy": "not_configured"}
    assert _discord_text(posts.calls[0]).endswith(
        "Finished: Translation. Finished: Library backup. Failed: Dub generation.")
    assert ns.flush() == {}


def test_repeats_are_grouped_by_kind_and_title_and_keep_their_names(env, dns, posts, no_timer):
    _write(env, discord=DISCORD)
    a = db.create_drama(title_en="Title A")
    b = db.create_drama(title_en="Title B")
    for _ in range(14):
        ns.notify_job_finished(f"Re-transcribe line (drama #{a})", "done")
    ns.notify_job_finished(f"Re-transcribe line (drama #{b})", "done")
    ns.notify_job_finished(f"Translation (drama #{a})", "done")
    assert no_timer == [ns.GROUP_WINDOW]   # only clean successes: they wait for repeats
    assert ns.flush()["discord"] == "sent"
    assert _discord_text(posts.calls[0]).endswith(
        "14 \u00d7 Re-transcribe line \u00b7 Title A \u00b7 all finished. "
        "Finished: Re-transcribe line - Title B. Finished: Translation - Title A.")


def test_many_groups_are_capped_and_repeated_failures_show_a_count():
    many = [("done", f"Finished: K{i}", "jobs", (f"K{i}", None)) for i in range(ns.MAX_GROUPS_NAMED + 2)]
    text = ns._summarize(many)
    assert text.endswith("2 more finished.") and "K5" not in text
    failed = [("error", "Failed: Export - T: Disk full", "jobs")] * 3
    assert ns._summarize(failed) == "Failed: Export - T: Disk full (\u00d73)."
    assert ns._summarize(failed + [("done", "Finished: A", "jobs", ("A", None))]) == \
        "Finished: A. Failed: Export - T: Disk full (\u00d73)."


def test_a_failure_does_not_wait_for_the_group_window(env, dns, posts, no_timer):
    _write(env, discord=DISCORD)
    ns.notify_job_finished("Export", "error", reason="Disk full")
    assert no_timer == [ns.BURST_WINDOW]
    ns.notify_job_finished("Translation", "done")      # joins the live short timer
    assert no_timer == [ns.BURST_WINDOW]


def _card(posts, i=-1):
    import json
    return json.loads(posts.calls[i]["data"].decode("utf-8"))["embeds"][0]


def _fields(card):
    return {f["name"]: f["value"] for f in card.get("fields", [])}


def test_a_finished_job_is_a_green_card_with_title_duration_and_counts(env, dns, posts, no_timer,
                                                                        monkeypatch):
    import background_jobs
    _write(env, discord=DISCORD)
    sid = db.get_or_create_series("Willow Lane")
    did = db.create_drama(title_en="Open Garden", series_id=sid)
    monkeypatch.setattr(background_jobs, "get_status", lambda job_id: {
        "started_at": 100.0, "finished_at": 292.0, "error": None,
        "result": {"translated_count": 40, "skipped_count": 2, "failed_count": 0,
                   "translated": ["line text must never show"], "line_count": True}})
    ns.notify_job_finished(f"Translation (drama #{did})", "done", job_id=f"translate_{did}")
    assert ns.flush()["discord"] == "sent"
    card = _card(posts)
    assert card["title"] == "Finished \u00b7 Translation" and card["color"] == ns.COLOR_OK
    assert card["description"] == "Open Garden (Willow Lane)"
    assert _fields(card) == {"Duration": "3 min 12 s", "Lines translated": "40", "Skipped": "2"}
    assert "url" not in card and "content" not in posts.calls[-1]["data"].decode()
    assert "never show" not in posts.calls[-1]["data"].decode()
    # ntfy gets plain text, never the card.
    _write(env, ntfy=NTFY)
    ns.notify_job_finished(f"Translation (drama #{did})", "done", job_id=f"translate_{did}")
    ns.flush()
    ntfy = [c for c in posts.calls if c["url"] == NTFY][-1]
    assert ntfy["data"].decode() == "Finished: Translation - Open Garden (Willow Lane)"


def test_errors_and_failures_are_amber_and_red_with_one_scrubbed_sentence(env, dns, posts,
                                                                          no_timer):
    _write(env, discord=DISCORD)
    ns.notify_job_finished("Export", "done", with_errors=True, reason="2 pages failed. See log.")
    ns.flush()
    amber = _card(posts)
    assert amber["title"] == "Finished with errors \u00b7 Export" and amber["color"] == ns.COLOR_WARN
    assert amber["description"] == "2 pages failed."
    ns.notify_job_finished("Export", "error",
                           reason="Could not write C:\\Users\\kae\\Secret\\out.srt with key "
                                  "sk-ant-ABCDEFGHIJKLMNOP. Second sentence.")
    ns.flush()
    red = _card(posts)
    assert red["title"] == "Failed \u00b7 Export" and red["color"] == ns.COLOR_FAIL
    _no_secret(red["description"])
    assert "kae" not in red["description"] and "Second" not in red["description"]


def test_the_card_links_the_title_only_with_a_remote_address(env, dns, posts, no_timer):
    _write(env, discord=DISCORD)
    did = db.create_drama(title_en="Open Garden")
    ns.notify_job_finished(f"Translation (drama #{did})", "done")
    ns.flush()
    assert "url" not in _card(posts)
    with open(env, "a", encoding="utf-8") as f:
        f.write("BAIHE_PUBLIC_URL=http://baihe.example.com\n")   # not https: not set up
    ns.notify_job_finished(f"Translation (drama #{did})", "done")
    ns.flush()
    assert "url" not in _card(posts)
    with open(env, "a", encoding="utf-8") as f:
        f.write("BAIHE_PUBLIC_URL=https://baihe.example.com\n")
    ns.notify_job_finished(f"Translation (drama #{did})", "done")
    ns.flush()
    assert _card(posts)["url"] == f"https://baihe.example.com/#/drama/{did}"
    # A title the card does not name gets no link either.
    ns.notify_job_finished("Translation (drama #99999)", "done")
    ns.flush()
    assert "url" not in _card(posts)


def test_the_card_names_who_ran_it_only_with_several_accounts_and_never_an_email(env, dns, posts,
                                                                                no_timer):
    _write(env, discord=DISCORD)
    first = db.auth_create_user("kae@example.com", "Kae")
    ns.notify_job_finished("Export", "done", owner_user_id=first)
    ns.flush()
    assert "Run by" not in _fields(_card(posts))
    second = db.auth_create_user("mei@example.com", "Mei")
    ns.notify_job_finished("Export", "done", owner_user_id=second)
    ns.flush()
    assert _fields(_card(posts))["Run by"] == "Mei"
    assert "example.com" not in posts.calls[-1]["data"].decode()
    ns.notify_job_finished("Export", "done", owner_user_id=None)   # a PC-owner run
    ns.flush()
    assert "Run by" not in _fields(_card(posts))
    # Google sign-in leaves the name empty: the card still says someone else ran it.
    nameless = db.auth_create_user("ann@example.com", "")
    ns.notify_job_finished("Export", "done", owner_user_id=nameless)
    ns.flush()
    assert _fields(_card(posts))["Run by"] == "A member (no name in People)"
    assert "example.com" not in posts.calls[-1]["data"].decode()
    admin = db.auth_create_user("boss@example.com", "", is_admin=True)
    ns.notify_job_finished("Export", "done", owner_user_id=admin)
    ns.flush()
    assert _fields(_card(posts))["Run by"] == "An admin (no name in People)"


def test_a_burst_card_takes_the_colour_of_its_worst_result(env, dns, posts, no_timer):
    _write(env, discord=DISCORD)
    ns.notify_job_finished("Export", "done")
    ns.notify_job_finished("Backup", "done")
    ns.flush()
    assert _card(posts)["color"] == ns.COLOR_OK
    ns.notify_job_finished("Export", "done")
    ns.notify_job_finished("Backup", "error")
    ns.flush()
    assert _card(posts)["color"] == ns.COLOR_FAIL
    ns.notify_job_finished("Export", "done")
    ns.notify_job_finished("Backup", "done", with_errors=True)
    ns.flush()
    assert _card(posts)["color"] == ns.COLOR_WARN


def test_events_and_the_test_message_stay_plain_text(env, dns, posts, no_timer):
    import json
    _write(env, discord=DISCORD)
    ns.notify_remote_access("Remote access stopped working.")
    ns.flush()
    body = json.loads(posts.calls[-1]["data"].decode("utf-8"))
    assert "embeds" not in body and body["content"].endswith("Remote access stopped working.")


def test_per_minute_cap(env, dns, posts, no_timer):
    _write(env, discord=DISCORD)
    for _ in range(ns.MAX_PER_MINUTE):
        ns.notify_job_finished("Translation", "done")
        assert ns.flush()["discord"] == "sent"
    ns.notify_job_finished("Translation", "done")
    assert ns.flush() == {}
    assert len(posts.calls) == ns.MAX_PER_MINUTE
    assert "rate limit" in _log_text()


def test_over_the_cap_only_clean_successes_are_dropped(env, dns, posts, no_timer):
    _write(env, discord=DISCORD)
    for _ in range(ns.MAX_PER_MINUTE):
        ns.notify_job_finished("Translation", "done")
        assert ns.flush()["discord"] == "sent"
    ns.notify_job_finished("Translation", "done")
    ns.notify_job_finished("Export (drama #1)", "error", reason="Disk full")
    ns.notify_waiting("Ep 1 is done.", push_text="Ep 1 is done.")
    assert ns.flush()["discord"] == "sent"
    text = _discord_text(posts.calls[-1])
    assert "Failed: Export" in text and "Disk full" in text and "Waiting for you" in text
    assert "Finished" not in text
    ns.notify_job_finished("Translation", "done")
    assert ns.flush() == {}


def test_a_short_success_stays_in_the_bell_and_a_long_one_pushes(env, dns, posts, no_timer,
                                                                 monkeypatch):
    import background_jobs
    _write(env, discord=DISCORD)
    times = {}
    monkeypatch.setattr(background_jobs, "get_status", lambda job_id: times.get(job_id))
    times["quick"] = {"started_at": 100.0, "finished_at": 100.0 + ns.SUCCESS_PUSH_MIN_SECONDS - 1}
    times["slow"] = {"started_at": 100.0, "finished_at": 100.0 + ns.SUCCESS_PUSH_MIN_SECONDS}
    ns.notify_job_finished("Re-time (drama #1)", "done", job_id="quick")
    assert no_timer == [] and ns.flush() == {}
    assert [e["kind"] for e in ns.list_recent({"user_id": None, "is_admin": True,
                                               "is_local_owner": True}, 10)] == ["job_done"]
    ns.notify_job_finished("Translation", "done", job_id="slow")
    assert ns.flush()["discord"] == "sent"
    # Failures and finishes with errors push however short the run was.
    ns.notify_job_finished("Re-time", "error", job_id="quick")
    ns.notify_job_finished("Re-time", "done", job_id="quick", with_errors=True)
    assert ns.flush()["discord"] == "sent"
    text = _discord_text(posts.calls[-1])
    assert "Failed: Re-time" in text and "Finished with errors: Re-time" in text


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


def test_job_end_queues_a_push_and_a_failed_send_never_breaks_the_job(env, job_hook, dns, posts,
                                                                      no_timer, monkeypatch):
    _write(env, discord=DISCORD)
    posts.exc = requests.ConnectionError(DISCORD)
    # The no-op job below ends at once; a zero threshold lets its success push.
    monkeypatch.setattr(ns, "SUCCESS_PUSH_MIN_SECONDS", 0.0)
    # The runner flips a job's status first and queues the push after, so
    # polling the status (or _pending) races the queueing. Wait instead for
    # notify_job_finished itself to return for both of this test's jobs; a
    # job thread left over from an earlier test in this worker may also
    # queue an event while the env fixture has notifications on, so only
    # this test's own messages are checked.
    ours = {"Translation (title #1)", "Dub generation (title #1)"}
    seen, both_queued = [], threading.Event()
    real = ns.notify_job_finished

    def recording(description, status, **kw):
        real(description, status, **kw)
        if description in ours:
            seen.append((description, status))
            if len(seen) == len(ours):
                both_queued.set()

    monkeypatch.setattr(ns, "notify_job_finished", recording)

    def boom():
        raise RuntimeError("real job error")

    job_launch.start_job("notify_t1", lambda: None, description="Translation (title #1)")
    job_launch.start_job("notify_t2", boom, description="Dub generation (title #1)")
    assert both_queued.wait(timeout=30), f"only {seen} were queued"
    assert background_jobs.get_status("notify_t1")["status"] == "done"
    job = background_jobs.get_status("notify_t2")
    assert job["status"] == "error" and "real job error" in job["error"]
    queued = [(status, msg) for status, msg, *_ in ns._pending
              if msg in ("Finished: Translation", "Failed: Dub generation")]
    assert sorted(queued) == [("done", "Finished: Translation"),
                              ("error", "Failed: Dub generation")]
    # The two jobs end in either order: the failure always leaves the short wait running.
    assert no_timer[-1] == ns.BURST_WINDOW and len(no_timer) <= 2
    assert ns.flush() == {"discord": "failed", "ntfy": "not_configured"}


def test_a_raising_notifier_never_breaks_the_job(env, job_hook, monkeypatch):
    def explode(*a, **k):
        raise RuntimeError("notifier down")

    monkeypatch.setattr(ns, "notify_job_finished", explode)
    job_launch.start_job("notify_t3", lambda: None, description="Translation")
    assert wait_for_job("notify_t3", timeout=4.0)["status"] == "done"


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
                        "ntfy_allow_local": False, "send_jobs": True, "send_chapters": True,
                        "send_remote": True, "show_private_titles": False,
                        "pc_name": "", "pc_name_default": ns._windows_name()}
    _no_secret(r.text)


def test_set_and_clear_round_trip(env):
    c = _client()
    r = c.post("/api/settings/notifications/discord", json={"value": DISCORD, "confirm": True})
    assert r.status_code == 200 and r.json() == {"channel": "discord", "configured": True}
    _no_secret(r.text)
    assert env_reader.resolve_env_names(ns.ENV_VARS["discord"]) == DISCORD
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


def test_auth_on_status_needs_admin_settings(env):
    _write(env, discord=DISCORD)
    c = TestClient(create_app(ApiSettings(auth_mode="on", allow_key_writes=True)),
                   base_url=REMOTE, raise_server_exceptions=False)
    assert c.get("/api/settings/notifications").status_code == 401
    kid = _session(False, "library.read", "jobs.start")
    assert c.get("/api/settings/notifications", headers=session_headers(kid)).status_code == 403
    admin = _session(True)
    r = c.get("/api/settings/notifications", headers=session_headers(admin))
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
        assert remote.post(path, json=body, headers=session_headers(admin)).status_code == 403
    assert not env.exists() and posts.calls == []
    local = TestClient(app, base_url=LOCAL, client=("127.0.0.1", 50000),
                       raise_server_exceptions=False)
    r = local.post("/api/settings/notifications/discord", json={"value": DISCORD, "confirm": True})
    assert r.status_code == 200 and r.json()["configured"] is True
    r = local.post("/api/settings/notifications/test", json={})
    assert r.status_code == 200 and r.json()["results"]["discord"] == "sent"


# --- categories, new chapters and the in-app list (item 5) --------------------

def _texts(principal=None):
    return [e["text"] for e in ns.list_recent(principal)]


def test_event_reaches_the_in_app_list_with_no_channel_configured(env, posts, no_timer):
    ns.notify_job_finished("Translation", "done", job_id="translate_1")
    ns.notify_job_finished("Dub generation", "error", job_id="dub_1")
    ns.notify_job_finished("Cancelled thing", "cancelled", job_id="x")
    items = ns.list_recent(None)
    assert [(e["kind"], e["text"]) for e in items] == [("job_failed", "Failed: Dub generation"),
                                                      ("job_done", "Finished: Translation")]
    assert items[0]["id"] > items[1]["id"] and set(items[0]) == {"id", "at", "kind", "text"}
    assert no_timer == [] and ns._pending == [] and posts.calls == []


def test_in_app_list_is_kept_when_automatic_sends_are_disabled(env, no_timer, monkeypatch):
    _write(env, discord=DISCORD)
    monkeypatch.setenv(ns.DISABLED_ENV, "1")
    ns.notify_job_finished("Translation", "done", job_id="translate_1")
    assert _texts() == ["Finished: Translation"] and ns._pending == []


def test_in_app_list_is_bounded(env, no_timer):
    for i in range(ns.RECENT_MAX + 5):
        ns.notify_job_finished(f"Job {i}", "done", job_id=f"job_{i}")
    texts = _texts()
    assert len(texts) == ns.RECENT_MAX and texts[0] == f"Finished: Job {ns.RECENT_MAX + 4}"


def test_in_app_list_follows_job_visibility(env, no_timer, monkeypatch):
    from services import ownership_service
    monkeypatch.setattr(ownership_service, "can_see_drama", lambda p, d: d == 1)
    ns.notify_job_finished("Translation (title #1)", "done", job_id="translate_1",
                           owner_user_id=7)
    ns.notify_job_finished("Translation (drama #2)", "done", job_id="translate_2",
                           owner_user_id=8)
    ns.notify_job_finished("Library backup", "done", job_id="library_backup", owner_user_id=8)
    ns.notify_job_finished("Library backup", "error", job_id="library_backup2",
                           owner_user_id=7)
    job_table._jobs["sources_chapter_check"] = {"status": "done", "result": {"new": 2}}
    try:
        ns.notify_job_finished("Checking tracked series for new chapters", "done",
                               job_id=ns.CHAPTER_CHECK_JOB_ID)
    finally:
        job_table._jobs.pop("sources_chapter_check", None)
    kid = {"user_id": 7, "is_admin": False, "is_local_owner": False}
    assert [e["kind"] for e in ns.list_recent(kid)] == ["chapters", "job_failed", "job_done"]
    assert len(ns.list_recent(None)) == 5
    assert len(ns.list_recent({"user_id": 1, "is_admin": True})) == 5


def _chapter_check(result, status="done"):
    job_table._jobs[ns.CHAPTER_CHECK_JOB_ID] = {"status": status, "result": result}
    try:
        ns.notify_job_finished("Checking tracked series for new chapters", status,
                               job_id=ns.CHAPTER_CHECK_JOB_ID)
    finally:
        job_table._jobs.pop(ns.CHAPTER_CHECK_JOB_ID, None)


def test_chapter_check_pushes_only_when_it_finds_new_chapters(env, no_timer):
    _write(env, discord=DISCORD)
    _chapter_check({"checked": 3, "new": 0, "errors": {}, "queued": []})
    _chapter_check({"checked": 0, "new": 0, "errors": {}, "queued": [], "skipped": True})
    _chapter_check(None)
    assert ns._pending == [] and _texts() == [] and no_timer == []
    _chapter_check({"checked": 3, "new": 1, "errors": {}, "queued": []})
    _chapter_check({"checked": 3, "new": 4, "errors": {}, "queued": []})
    assert [m for _s, m, *_ in ns._pending] == ["1 new chapter found", "4 new chapters found"]
    assert _texts() == ["4 new chapters found", "1 new chapter found"]
    _chapter_check(None, status="error")
    assert ns._pending[-1][:2] == ("error", "Failed: Checking tracked series for new chapters")


def test_categories_switch_external_pushes_but_not_the_in_app_list(env, no_timer):
    _write(env, discord=DISCORD)
    assert ns.get_categories() == {"jobs": True, "chapters": True, "remote": True}
    ns.set_categories(jobs=False)
    ns.notify_job_finished("Translation", "done", job_id="translate_1")
    _chapter_check({"new": 2})
    assert [c for _s, _m, c, *_ in ns._pending] == ["chapters"]
    ns.set_categories(jobs=True, chapters=False)
    assert ns.get_categories() == {"jobs": True, "chapters": False, "remote": True}
    _chapter_check({"new": 3})
    ns.notify_job_finished("Dub generation", "error", job_id="dub_1")
    assert [c for _s, _m, c, *_ in ns._pending] == ["chapters", "jobs"]
    assert _texts() == ["Failed: Dub generation", "3 new chapters found",
                        "2 new chapters found", "Finished: Translation"]


def test_disabled_category_never_reaches_discord(env, dns, posts, no_timer):
    _write(env, discord=DISCORD)
    ns.set_categories(jobs=False)
    ns.notify_job_finished("Translation", "done", job_id="translate_1")
    assert ns.flush() == {} and posts.calls == []


def test_translation_completed_reaches_discord_with_the_right_content(env, dns, posts, no_timer):
    import json
    _write(env, discord=DISCORD)
    ns.notify_job_finished("Translation (drama #999)", "done", job_id="translate_999")
    assert ns.flush() == {"discord": "sent", "ntfy": "not_configured"}
    body = json.loads(posts.calls[0]["data"].decode("utf-8"))
    assert body["allowed_mentions"] == {"parse": []}
    assert body["embeds"][0]["title"] == "Finished \u00b7 Translation"
    assert body["embeds"][0]["color"] == ns.COLOR_OK


def test_mixed_burst_summary():
    assert ns._summarize([("done", "Finished: Translation", "jobs"),
                          ("done", "2 new chapters found", "chapters")]) == \
        "Finished: Translation. 2 new chapters found."
    assert ns._summarize([("done", "a", "jobs"), ("error", "b", "jobs"),
                          ("done", "3 new chapters found", "chapters")]) == \
        "a. b. 3 new chapters found."


def test_categories_route_is_pc_only(env):
    r = _client(key_writes=False).post("/api/settings/notifications/categories",
                                       json={"chapters": False})
    assert r.status_code == 200
    assert r.json()["send_chapters"] is False and r.json()["send_jobs"] is True
    r = _client().post("/api/settings/notifications/categories", json={"jobs": "no"})
    assert r.status_code == 422
    r = _client().post("/api/settings/notifications/categories", json={"other": True})
    assert r.status_code == 422
    remote = TestClient(create_app(ApiSettings(auth_mode="on", allow_key_writes=True)),
                        base_url=REMOTE, raise_server_exceptions=False)
    admin = _session(True)
    assert remote.post("/api/settings/notifications/categories", json={"jobs": False},
                       headers=session_headers(admin)).status_code == 403
    assert ns.get_categories() == {"jobs": True, "chapters": False, "remote": True}


def test_recent_route_lists_events_without_job_ids(env, no_timer):
    ns.notify_job_finished("Translation", "done", job_id="translate_1")
    r = _client().get("/api/notifications")
    assert r.status_code == 200
    items = r.json()["items"]
    assert [i["text"] for i in items] == ["Finished: Translation"]
    assert "translate_1" not in r.text


def test_auth_on_recent_route_needs_library_read_and_filters(env, no_timer):
    c = TestClient(create_app(ApiSettings(auth_mode="on")), base_url=REMOTE,
                   raise_server_exceptions=False)
    assert c.get("/api/notifications").status_code == 401
    kid = _session(False, "library.read")
    kid_id = auth_service.find_user_by_email("kid@example.com")["id"]
    ns.notify_job_finished("Library backup", "done", job_id="library_backup", owner_user_id=None)
    ns.notify_job_finished("Discover extract", "done", job_id="discover_x",
                           owner_user_id=kid_id)
    r = c.get("/api/notifications", headers=session_headers(kid))
    assert r.status_code == 200
    assert [i["text"] for i in r.json()["items"]] == ["Finished: Discover extract"]
    for p in ("library.read",):
        auth_service.revoke_permission(kid_id, p)
    assert c.get("/api/notifications", headers=session_headers(kid)).status_code == 403


def test_push_leaves_out_a_private_drama_title_but_the_in_app_list_keeps_it(env, no_timer):
    _write(env, discord=DISCORD)
    shared = db.create_drama(title_en="Open Garden")
    private = db.create_drama(title_en="Secret Garden", owner_user_id=5, is_private=1)
    ns.notify_job_finished(f"Translation (drama #{shared})", "done", job_id=f"translate_{shared}")
    ns.notify_job_finished(f"Translation (drama #{private})", "done",
                           job_id=f"translate_{private}", owner_user_id=5)
    assert [m for _s, m, *_ in ns._pending] == ["Finished: Translation - Open Garden",
                                                "Finished: Translation"]
    assert _texts() == ["Finished: Translation - Secret Garden",
                        "Finished: Translation - Open Garden"]


def test_messages_name_the_series_and_the_private_switch_lifts_the_household_rule(env, no_timer):
    _write(env, discord=DISCORD)
    open_series = db.get_or_create_series("Willow Lane")
    secret_series = db.get_or_create_series("Hidden Row", owner_user_id=5, is_private=True)
    shared = db.create_drama(title_en="Open Garden", series_id=open_series)
    in_secret = db.create_drama(title_en="Quiet Room", series_id=secret_series)
    private = db.create_drama(title_en="Secret Garden", owner_user_id=5, is_private=1)

    def pushes():
        ns._pending.clear()
        for did in (shared, in_secret, private):
            ns.notify_job_finished(f"Translation (drama #{did})", "done", job_id=f"translate_{did}")
        return [m for _s, m, *_ in ns._pending]

    assert ns.get_status()["show_private_titles"] is False
    assert pushes() == ["Finished: Translation - Open Garden (Willow Lane)",
                        "Finished: Translation", "Finished: Translation"]
    assert ns.set_categories(private_titles=True)["show_private_titles"] is True
    assert pushes() == ["Finished: Translation - Open Garden (Willow Lane)",
                        "Finished: Translation - Quiet Room (Hidden Row)",
                        "Finished: Translation - Secret Garden"]
    assert ns.set_categories(jobs=False)["show_private_titles"] is True   # untouched
    assert ns.set_categories(private_titles=False)["show_private_titles"] is False


def test_private_titles_switch_route_is_pc_only(env):
    remote = TestClient(create_app(ApiSettings(auth_mode="on", allow_key_writes=True)),
                        base_url=REMOTE, raise_server_exceptions=False)
    assert remote.post("/api/settings/notifications/categories", json={"private_titles": True},
                       headers=session_headers(_session(True))).status_code == 403
    assert ns.show_private_titles() is False
    r = _client().post("/api/settings/notifications/categories", json={"private_titles": True})
    assert r.status_code == 200 and r.json()["show_private_titles"] is True


def test_pc_name_replaces_the_windows_name_until_cleared(env, monkeypatch):
    import json
    monkeypatch.setattr(ns.socket, "gethostname", lambda: "DESKTOP-EXAMPLE")
    card = {"title": "Finished", "color": ns.COLOR_OK}

    def footer():
        return json.loads(ns._payload("discord", "x", card)[0])["embeds"][0]["footer"]["text"]

    assert footer() == "Sent from DESKTOP-EXAMPLE"
    status = ns.set_categories(pc_name="  V100  ")
    assert status["pc_name"] == "V100" and status["pc_name_default"] == "DESKTOP-EXAMPLE"
    assert footer() == "Sent from V100"
    assert ns.set_categories(jobs=False)["pc_name"] == "V100"        # untouched
    assert ns.set_categories(pc_name="x" * 90)["pc_name"] == "x" * 39 + "\u2026"
    assert ns.set_categories(pc_name="")["pc_name"] == ""
    assert footer() == "Sent from DESKTOP-EXAMPLE"


def test_pc_name_route_is_pc_only_and_bounded(env):
    remote = TestClient(create_app(ApiSettings(auth_mode="on", allow_key_writes=True)),
                        base_url=REMOTE, raise_server_exceptions=False)
    assert remote.post("/api/settings/notifications/categories", json={"pc_name": "Mine"},
                       headers=session_headers(_session(True))).status_code == 403
    assert ns.saved_pc_name() == ""
    c = _client()
    assert c.post("/api/settings/notifications/categories",
                  json={"pc_name": "x" * 41}).status_code == 422
    assert c.post("/api/settings/notifications/categories",
                  json={"pc_name": 5}).status_code == 422
    r = c.post("/api/settings/notifications/categories", json={"pc_name": "3080"})
    assert r.status_code == 200 and r.json()["pc_name"] == "3080"


def test_ids_increase_but_do_not_count_hidden_events(env, no_timer, monkeypatch):
    clock = iter([1000.0, 1000.0, 1000.0, 1000.001, 2000.0])
    monkeypatch.setattr(ns.time, "time", lambda: next(clock))
    for i in range(5):
        ns.notify_job_finished(f"Job {i}", "done", job_id=f"job_{i}")
    ids = [e["id"] for e in reversed(ns.list_recent(None))]
    assert ids == [1000000, 1000001, 1000002, 1000003, 2000000]


def test_other_users_jobs_do_not_push_out_a_viewers_own_events(env, no_timer):
    kid = {"user_id": 7, "is_admin": False, "is_local_owner": False}
    ns.notify_job_finished("Discover extract", "done", job_id="discover_mine", owner_user_id=7)
    for i in range(ns.RECENT_MAX * 2):
        ns.notify_job_finished("Library backup", "done", job_id=f"library_{i}")
    assert [e["text"] for e in ns.list_recent(kid)] == ["Finished: Discover extract"]
    assert len(ns.list_recent(None)) == ns.RECENT_MAX


def _fire_watchdog_then_fail(monkeypatch, exc):
    import requests

    timers = []

    class _Timer:
        def __init__(self, interval, fn):
            self.fn = fn
            self.daemon = False
            self.name = ""
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            pass

    def post(self, *a, **kw):
        timers[0].fn()   # the deadline fires while the request is in flight
        raise exc

    monkeypatch.setattr(ns.threading, "Timer", _Timer)
    monkeypatch.setattr(requests.Session, "post", post)


def test_a_reset_after_the_deadline_is_reported_as_the_timeout(monkeypatch):
    _fire_watchdog_then_fail(monkeypatch, ConnectionResetError("reset"))
    with pytest.raises(TimeoutError, match="send deadline passed"):
        ns._pinned_post("http://slow.test:80/t", "127.0.0.1", b"x", {})


def test_a_reset_before_the_deadline_stays_a_connection_error(monkeypatch):
    import requests

    def post(self, *a, **kw):
        raise ConnectionResetError("reset")

    monkeypatch.setattr(requests.Session, "post", post)
    with pytest.raises(ConnectionResetError):
        ns._pinned_post("http://slow.test:80/t", "127.0.0.1", b"x", {})


# --- "Waiting for you" entries ---------------------------------------------------

def _waiting(principal=None):
    return [e for e in ns.list_recent(principal) if e["kind"] == "waiting"]


def test_waiting_entry_kind_text_target_and_push(env, no_timer):
    _write(env, discord=DISCORD)
    ns.notify_waiting("3 glossary names to review - Garden", push_text="3 glossary names to review",
                      target={"page": "glossary", "drama_id": 4})
    (entry,) = _waiting()
    assert entry["kind"] == "waiting"
    assert entry["text"] == "Waiting for you: 3 glossary names to review - Garden"
    assert entry["target"] == {"page": "glossary", "drama_id": 4}
    assert ns._pending == [("waiting", "Waiting for you: 3 glossary names to review", "jobs")]
    assert len(no_timer) == 1


@pytest.mark.parametrize("target, kept", [
    ({"page": "review", "drama_id": 9}, {"page": "review", "drama_id": 9}),
    ({"page": "series", "series_id": 2}, {"page": "series", "series_id": 2}),
    ({"page": "jobs"}, {"page": "jobs"}),
    # Only the page's own id is kept, so nothing else can ride along.
    ({"page": "jobs", "url": "https://evil.example/x", "drama_id": 3}, {"page": "jobs"}),
    ({"page": "glossary", "drama_id": 4, "series_id": 2, "path": "C:\\x"},
     {"page": "glossary", "drama_id": 4}),
    ({"page": "settings"}, None),
    ({"page": "glossary"}, None),
    ({"page": "glossary", "drama_id": "4"}, None),
    ({"page": "glossary", "drama_id": True}, None),
    ({"page": "glossary", "drama_id": 0}, None),
    ({"page": "series", "drama_id": 2}, None),
    ("jobs", None),
    (None, None),
])
def test_waiting_target_is_validated_where_it_enters(env, no_timer, target, kept):
    assert ns.clean_target(target) == kept
    ns.notify_waiting("Something", push_text="Something", target=target)
    (entry,) = _waiting()
    assert entry.get("target") == kept
    if kept is None:
        assert set(entry) == {"id", "at", "kind", "text"}


def test_waiting_entry_follows_owner_and_job_visibility(env, no_timer, monkeypatch):
    from services import ownership_service
    monkeypatch.setattr(ownership_service, "can_see_drama", lambda p, d: d == 1)
    ns.notify_waiting("Mine", push_text="Mine", owner_user_id=7)
    ns.notify_waiting("Theirs", push_text="Theirs", owner_user_id=8)
    ns.notify_waiting("PC only", push_text="PC only")
    ns.notify_waiting("Title one", push_text="Title one", job_id="lines_glossary_1", owner_user_id=8)
    ns.notify_waiting("Title two", push_text="Title two", job_id="lines_glossary_2", owner_user_id=7)
    kid = {"user_id": 7, "is_admin": False, "is_local_owner": False}
    assert [e["text"] for e in _waiting(kid)] == ["Waiting for you: Title one", "Waiting for you: Mine"]
    assert len(_waiting(None)) == 5
    assert len(_waiting({"user_id": 1, "is_admin": True})) == 5


def test_waiting_push_is_redacted_tidy_and_carries_no_link(env, no_timer):
    _write(env, discord=DISCORD)
    text = ("Ep 1 stopped at translate: 401 for sk-ant-api03-SECRETSECRETSECRETSECRET\n"
            f"see https://baihe.example.com/#/jobs and {DISCORD}\x07 Retry it.")
    ns.notify_waiting(text, push_text=text)
    ((_s, pushed, _c),) = ns._pending
    (entry,) = _waiting()
    for out in (pushed, entry["text"]):
        assert out.startswith("Waiting for you: Ep 1 stopped at translate:")
        assert "sk-ant" not in out and "SECRETSECRET" not in out
        assert "http" not in out and "baihe.example.com" not in out and "discord.com" not in out
        assert "\n" not in out and "\x07" not in out
        _no_secret(out)
    ns.notify_waiting("x" * 2000, push_text="x" * 2000)
    assert len(_waiting()[0]["text"]) <= len(ns.WAITING_PREFIX) + ns._WAITING_MAX


def test_waiting_push_off_with_the_jobs_category_or_when_disabled(env, no_timer, monkeypatch):
    _write(env, discord=DISCORD)
    ns.set_categories(jobs=False)
    ns.notify_waiting("A", push_text="A")
    assert ns._pending == [] and no_timer == []
    ns.set_categories(jobs=True)
    monkeypatch.setenv(ns.DISABLED_ENV, "1")
    ns.notify_waiting("B", push_text="B")
    assert ns._pending == [] and no_timer == []
    monkeypatch.delenv(ns.DISABLED_ENV)
    env.write_text("")
    ns.notify_waiting("C", push_text="C")
    assert ns._pending == [] and no_timer == []
    # The bell lists every one of them.
    assert [e["text"] for e in _waiting()] == [f"Waiting for you: {c}" for c in "CBA"]


def test_waiting_never_raises(env, no_timer, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(ns, "_record", boom)
    assert ns.notify_waiting("A", push_text="A") is None
    monkeypatch.setattr(ns, "_record", lambda *a, **k: None)
    monkeypatch.setattr(ns, "get_categories", boom)
    assert ns.notify_waiting("A", push_text="A", target=object()) is None
    assert ns.notify_job_finished("Translation", "done", waiting={"cap": True}) is None


def test_waiting_messages_are_never_folded_into_the_job_count():
    w = ("waiting", "Waiting for you: Ep 1 is done. Press Continue to run the rest.", "jobs")
    assert ns._summarize([w]) == w[1]
    assert ns._summarize([("done", "Finished: Translation", "jobs"), w]) == \
        "Finished: Translation. Waiting for you: Ep 1 is done. Press Continue to run the rest."
    assert ns._summarize([("done", "a", "jobs"), w, ("error", "b", "jobs"),
                          ("waiting", "Waiting for you: 2 glossary names to review", "jobs"),
                          ("done", "3 new chapters found", "chapters")]) == (
        "a. Waiting for you: Ep 1 is done. Press Continue to run the rest. b. "
        "Waiting for you: 2 glossary names to review. 3 new chapters found.")
    assert ns._summarize([w, ("waiting", "Waiting for you: The queue stopped.", "jobs")]) == \
        "Waiting for you: Ep 1 is done. Press Continue to run the rest. Waiting for you: The queue stopped."


def test_a_done_job_with_a_waiting_note_says_waiting_and_hides_a_private_title(env, no_timer):
    _write(env, discord=DISCORD)
    shared = db.create_drama(title_en="Open Garden")
    private = db.create_drama(title_en="Secret Garden", owner_user_id=5, is_private=1)
    note = {"text": "2 glossary names to review", "target": {"page": "glossary", "drama_id": shared}}
    ns.notify_job_finished(f"Glossary from lines (title #{shared})", "done",
                           job_id=f"lines_glossary_{shared}", waiting=note)
    ns.notify_job_finished(f"Glossary from lines (title #{private})", "done",
                           job_id=f"lines_glossary_{private}", owner_user_id=5,
                           waiting={**note, "target": {"page": "glossary", "drama_id": private}})
    assert [m for _s, m, *_ in ns._pending] == [
        "Waiting for you: 2 glossary names to review - Open Garden",
        "Waiting for you: 2 glossary names to review"]
    items = ns.list_recent(None)
    assert [(e["kind"], e["text"], e["target"]) for e in items] == [
        ("waiting", "Waiting for you: 2 glossary names to review - Secret Garden",
         {"page": "glossary", "drama_id": private}),
        ("waiting", "Waiting for you: 2 glossary names to review - Open Garden",
         {"page": "glossary", "drama_id": shared})]
    # A failed job ignores a note; a note without words says what stopped.
    ns.notify_job_finished(f"Translation (drama #{shared})", "error", waiting=note)
    ns.notify_job_finished(f"Translation (drama #{shared})", "done", waiting={"cap": True})
    ns.notify_job_finished(f"Translation (drama #{shared})", "done",
                           waiting={"reason": "3 requests failed in a row"})
    assert [e["text"] for e in ns.list_recent(None)][:3] == [
        "Waiting for you: Translation stopped - Open Garden. 3 requests failed in a row",
        "Waiting for you: Translation stopped at the spending cap - Open Garden",
        "Failed: Translation - Open Garden"]


def _finish_job(job_id, result, description):
    """Runs a thread job that returns `result` through the real hook; waits for its notice."""
    told = threading.Event()
    real = ns.notify_job_finished

    def recording(desc, status, **kw):
        real(desc, status, **kw)
        if desc == description:
            told.set()
    import unittest.mock
    with unittest.mock.patch.object(ns, "notify_job_finished", recording):
        background_jobs.clear_job(job_id)
        job_launch.start_job(job_id, lambda: background_jobs.set_result(job_id, result),
                             description=description)
        assert told.wait(timeout=30)
    background_jobs.clear_job(job_id)
    return ns.list_recent(None)[0]


def test_a_job_stopped_at_a_spending_cap_is_waiting_not_finished(env, job_hook, no_timer):
    from engine_backends import paid_calls
    did = db.create_drama(title_en="Moonlit Garden")
    desc = f"Translation (drama #{did})"
    cases = [
        ({"cap_reached": 0.42, "translated_count": 3},
         "Waiting for you: Translation stopped at the spending cap - Moonlit Garden"),
        ({"stopped": paid_calls.CAP_STOP_REASON},
         "Waiting for you: Translation stopped at the spending cap - Moonlit Garden"),
        ({"stopped": paid_calls.failure_stop_reason(3)},
         "Waiting for you: Translation stopped - Moonlit Garden. 3 requests failed in a row"),
        ({"stopped": "cancelled"}, "Finished: Translation - Moonlit Garden"),
        ({"translated_count": 3}, "Finished: Translation - Moonlit Garden"),
    ]
    for i, (result, text) in enumerate(cases):
        entry = _finish_job(f"notify_cap_{i}", result, desc)
        assert entry["text"] == text, result
        assert entry.get("target") == ({"page": "jobs"} if text.startswith("Waiting")
                                           else {"page": "watch", "drama_id": did} if text.startswith("Finished")
                                           else None)


def test_waiting_note_reads_only_what_the_result_says():
    from engine_backends import paid_calls
    from services import jobs_service
    target = {"page": "glossary", "drama_id": 3}
    assert jobs_service.waiting_note({"waiting": {"text": "1 glossary name to review",
                                                  "target": target}}) == \
        {"text": "1 glossary name to review", "target": target}
    assert jobs_service.waiting_note({"cap_reached": 0}) == {"cap": True, "target": {"page": "jobs"}}
    assert jobs_service.waiting_note({"cap_reached": True}) is None
    assert jobs_service.waiting_note({"cap_reached": None}) is None
    assert jobs_service.waiting_note({"stopped": paid_calls.CAP_STOP_REASON})["cap"] is True
    assert jobs_service.waiting_note({"stopped": paid_calls.RATE_LIMIT_STOP_REASON})["reason"] == \
        "The provider keeps rate-limiting you; try later or switch engine"
    assert jobs_service.waiting_note(
        {"stopped": "Stopped checking: 3 status checks in a row failed. Press Check now to try again."}
    )["reason"].startswith("Stopped checking: 3 status checks")
    for nothing in (None, [], {}, {"stopped": "cancelled"}, {"stopped": "cap"},
                    {"waiting": {"text": "  "}}, {"waiting": "names"}):
        assert jobs_service.waiting_note(nothing) is None


def test_notification_event_schema_takes_waiting_and_its_target_only():
    from pydantic import ValidationError
    from api.schemas import NotificationEvent
    e = NotificationEvent(id=1, at=1.0, kind="waiting", text="Waiting for you: x",
                          target={"page": "glossary", "drama_id": 3})
    assert e.model_dump(exclude_none=True)["target"] == {"page": "glossary", "drama_id": 3}
    NotificationEvent(id=1, at=1.0, kind="waiting", text="x", target={"page": "jobs"})
    for bad in ({"kind": "nope"},
                {"kind": "waiting", "target": {"page": "elsewhere"}},
                {"kind": "waiting", "target": {"page": "jobs", "url": "https://x.example"}},
                {"kind": "waiting", "target": {"page": "review", "drama_id": "3"}},
                {"kind": "waiting", "target": {"page": "review", "drama_id": 0}}):
        with pytest.raises(ValidationError):
            NotificationEvent(**{"id": 1, "at": 1.0, "text": "x", **bad})


def test_recent_route_returns_a_waiting_entry_with_its_target(env, no_timer):
    ns.notify_waiting("The queue stopped because an item failed. Start the queue to go on.",
                      push_text="The queue stopped because an item failed. Start the queue to go on.",
                      target={"page": "jobs"})
    r = _client().get("/api/notifications")
    assert r.status_code == 200
    assert r.json()["items"] == [{"id": r.json()["items"][0]["id"], "at": r.json()["items"][0]["at"],
                                  "kind": "waiting", "target": {"page": "jobs"},
                                  "text": "Waiting for you: The queue stopped because an item "
                                          "failed. Start the queue to go on."}]


@pytest.mark.parametrize("kept", [3, 7, 12, 16])
def test_waiting_text_redacts_a_key_that_straddles_the_length_cut(env, no_timer, kept):
    """`kept`: how many characters of the key would be left before the cut."""
    _write(env, discord=DISCORD)
    key = "sk-ant-api03-" + "K" * 40
    text = "x" * (ns._WAITING_MAX - 2 - kept) + " " + key + " tail"
    ns.notify_waiting(text, push_text=text)
    ((_s, pushed, _c),) = ns._pending
    (entry,) = _waiting()
    for out in (pushed, entry["text"]):
        assert "sk-" not in out and "KK" not in out
        assert len(out) <= len(ns.WAITING_PREFIX) + ns._WAITING_MAX


def test_waiting_job_message_is_the_bell_sentence(env):
    assert ns.waiting_job_message("Translation", {"cap": True}) == \
        "Waiting for you: Translation stopped at the spending cap"


@pytest.mark.parametrize("guest", [True, False, "raises"])
def test_the_desktop_pop_up_names_a_private_title_only_off_the_guest_profile(env, monkeypatch, guest):
    from services import pc_profile_service

    def active():
        if guest == "raises":
            raise RuntimeError("settings unreadable")
        return guest
    monkeypatch.setattr(pc_profile_service, "guest_active", active)
    shared = db.create_drama(title_en="Open Garden")
    private = db.create_drama(title_en="Secret Garden", owner_user_id=5, is_private=1)
    note = {"text": "2 glossary names to review"}
    assert ns.waiting_job_message(f"Glossary from lines (title #{shared})", note) == \
        "Waiting for you: 2 glossary names to review - Open Garden"
    named = "" if guest is not False else " - Secret Garden"
    assert ns.waiting_job_message(f"Glossary from lines (title #{private})", note) == \
        f"Waiting for you: 2 glossary names to review{named}"


def test_a_hold_entry_that_names_a_title_follows_that_title_as_well_as_its_target(env, no_timer):
    from services import ownership_service
    member = auth_service.add_user("reader@example.com")
    kid = {"user_id": member["id"], "is_admin": False, "is_local_owner": False}
    admin = {"user_id": 999, "is_admin": True, "is_local_owner": False}
    owner = {"user_id": None, "is_admin": False, "is_local_owner": True}
    sid = db.get_or_create_series("Willow Lane")
    ep = db.create_drama(title_en="Willow Lane 1", series_id=sid)
    ns.notify_waiting("Willow Lane 1 stopped at translate: engine said no", push_text="x",
                      owner_user_id=member["id"], target={"page": "series", "series_id": sid},
                      names_drama_id=ep)
    (entry,) = _waiting(kid)
    assert set(entry) == {"id", "at", "kind", "text", "target"}
    assert entry["target"] == {"page": "series", "series_id": sid}
    (item,) = _client().get("/api/notifications").json()["items"]
    assert set(item) == {"id", "at", "kind", "text", "target"}
    db.update_drama(ep, is_private=1, owner_user_id=5)
    assert ownership_service.can_see(kid, "series", sid)
    assert _waiting(kid) == []
    for viewer in (admin, owner, None):
        assert [e["text"] for e in _waiting(viewer)] == [entry["text"]]


def test_a_hold_entry_naming_a_deleted_title_is_hidden_and_a_bad_id_is_dropped(env, no_timer):
    member = auth_service.add_user("reader@example.com")
    kid = {"user_id": member["id"], "is_admin": False, "is_local_owner": False}
    sid = db.get_or_create_series("Willow Lane")
    gone = db.create_drama(title_en="Willow Lane 2", series_id=sid)
    db.delete_drama(gone)
    ns.notify_waiting("Gone", push_text="x", owner_user_id=member["id"],
                      target={"page": "series", "series_id": sid}, names_drama_id=gone)
    assert _waiting(kid) == []
    for bad in ("3", True, 0, -1, 2.0, None):
        ns.reset_for_tests()
        ns.notify_waiting("Kept", push_text="x", owner_user_id=member["id"],
                          target={"page": "series", "series_id": sid}, names_drama_id=bad)
        assert [e["text"] for e in _waiting(kid)] == ["Waiting for you: Kept"], bad
        assert "names_drama_id" not in ns._recent[-1], bad


@pytest.mark.parametrize("stopped, push_tail", [
    (paid_calls.failure_stop_reason(3), ". 3 requests failed in a row"),
    (run_limits.RATE_LIMIT_STOP_REASON,
     ". The provider keeps rate-limiting you; try later or switch engine"),
    ("Stopped: 3 batches in a row failed (connection to 192.168.1.20:11434 refused)", ""),
    ("Stopped: HTTPConnectionPool(host='nas.local', port=11434): Max retries exceeded", ""),
])
def test_a_stopped_jobs_push_gives_only_baihes_own_stop_reasons(env, no_timer, stopped, push_tail):
    _write(env, discord=DISCORD)
    did = db.create_drama(title_en="Open Garden")
    note = jobs_service.waiting_note({"stopped": stopped})
    assert note is not None
    ns.notify_job_finished(f"Translation (drama #{did})", "done", job_id=f"translate_{did}",
                           waiting=note)
    ((_s, pushed, _c),) = ns._pending
    assert pushed == "Waiting for you: Translation stopped - Open Garden" + push_tail
    (entry,) = _waiting()
    assert entry["text"] == f"Waiting for you: Translation stopped - Open Garden. {note['reason']}"
    if not push_tail:
        assert "11434" in entry["text"]


def test_waiting_note_marks_only_baihes_own_stop_sentences():
    for own in (paid_calls.failure_stop_reason(3), paid_calls.failure_stop_reason(12),
                run_limits.RATE_LIMIT_STOP_REASON):
        note = jobs_service.waiting_note({"stopped": own})
        assert note is not None and note["own_reason"] is True, own
    for other in ("Stopped: 3 titles in a row failed",
                  "Stopped: 3 batches in a row failed (timed out)",
                  "Stopped checking: 3 status checks in a row failed. Press Check now to try again.",
                  "Stopped: engine said no",
                  paid_calls.failure_stop_reason(3) + " (at 192.168.1.20)",
                  "Stopped: 03 requests failed in a row",
                  "Stopped: " + run_limits.RATE_LIMIT_STOP_REASON):
        note = jobs_service.waiting_note({"stopped": other})
        assert note is not None and not note.get("own_reason"), other
    # A result's own note is rebuilt from its text and target, so it cannot set the mark.
    assert jobs_service.waiting_note({"waiting": {"text": "1 glossary name to review",
                                                  "own_reason": True, "reason": "x"}}) == \
        {"text": "1 glossary name to review", "target": None}
    note = jobs_service.waiting_note(
        {"stopped": "Stopped: Stopped: 3 batches in a row failed (timed out)"})
    assert note is not None and note["reason"] == "3 batches in a row failed (timed out)"


def test_a_done_job_with_errors_and_a_waiting_note_says_waiting(env, no_timer):
    _write(env, discord=DISCORD)
    ns.notify_job_finished("Translation", "done", with_errors=True, waiting={"cap": True})
    assert _texts() == ["Waiting for you: Translation stopped at the spending cap"]
    assert [m for _s, m, *_ in ns._pending] == \
        ["Waiting for you: Translation stopped at the spending cap"]


def test_a_names_result_that_also_stopped_early_says_the_names_sentence():
    note = jobs_service.waiting_note({"waiting": {"text": "4 glossary names to review"},
                                      "stopped": paid_calls.CAP_STOP_REASON})
    assert note == {"text": "4 glossary names to review", "target": None}


def test_resource_alert_routes_read_and_change_rules(env):
    c = _client(key_writes=False)
    rows = {r["id"]: r for r in c.get("/api/settings/resource-alerts").json()["rules"]}
    assert rows["cpu"]["enabled"] is False and len(rows) == 7
    r = c.post("/api/settings/resource-alerts", json={"rules": {"cpu": {"enabled": True, "limit": 90}}})
    assert r.status_code == 200
    assert {x["id"]: x for x in r.json()["rules"]}["cpu"]["limit"] == 90
    assert c.post("/api/settings/resource-alerts", json={"rules": {"cpu": {"limit": 5}}}).status_code == 422
    assert c.post("/api/settings/resource-alerts", json={"rules": {"nope": {}}}).status_code == 422
    assert c.post("/api/settings/resource-alerts", json={"rules": {"cpu": {"x": 1}}}).status_code == 422


def test_resource_alert_changes_are_pc_only(env):
    remote = TestClient(create_app(ApiSettings(auth_mode="on")), base_url=REMOTE, raise_server_exceptions=False)
    admin = _session(True)
    assert remote.post("/api/settings/resource-alerts", json={"rules": {"cpu": {"enabled": True}}},
                       headers=session_headers(admin)).status_code == 403


def test_a_finished_job_opens_the_title_it_finished(env, job_hook, no_timer):
    video = db.create_drama(title_en="Moonlit Garden", media_type="video_drama")
    novel = db.create_drama(title_en="Quiet Pages", media_type="novel")
    narrated = db.create_drama(title_en="Spoken Pages", media_type="audio_drama", content_mode="novel_narration")
    assert _finish_job("open_video", {}, f"Translation (drama #{video})")["target"] == {"page": "watch", "drama_id": video}
    assert _finish_job("open_novel", {}, f"Translation (drama #{novel})")["target"] == {"page": "title", "drama_id": novel}
    assert _finish_job("open_narrated", {}, f"Translation (drama #{narrated})")["target"] == {"page": "title", "drama_id": narrated}
    assert "target" not in _finish_job("open_none", {}, "Housekeeping")
