"""The Diagnostics model download: what can be fetched, who may start it, and how a
job ends. The Hub and ModelScope fetches are replaced; no network, no model."""
import sys
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service
from services import model_download_service as svc
from tests.api_helpers import session_headers
from tests.job_helpers import wait_for_job

REMOTE = "https://baihe.example.com"
PC = {"X-Baihe-Local": "1"}   # what the app's PC-only calls send
LOCAL = dict(base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000))


@pytest.fixture
def models(isolated_db, monkeypatch):
    """Every catalog model starts not downloaded; a fetch marks it downloaded unless it fails."""
    state = {mid: False for mid in svc.CATALOG}
    fetched = []
    patched = {}
    for mid, entry in svc.CATALOG.items():
        def fetch(report, cancelled, mid=mid):
            report(0.5, "halfway")
            fetched.append(mid)
            state[mid] = True
        patched[mid] = {**entry, "ready": lambda mid=mid: state[mid], "fetch": fetch}
    monkeypatch.setattr(svc, "CATALOG", patched)
    yield state, fetched
    for mid in patched:
        background_jobs.clear_job(svc.job_id(mid))


@pytest.fixture
def client(models):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def test_lists_each_model_with_whether_it_is_on_disk(client, models):
    state, _ = models
    state["sensevoice"] = True
    body = client.get("/api/diagnostics/model-downloads").json()
    assert {m["id"]: m["downloaded"] for m in body["models"]} == {
        "qwen3_asr": False, "sensevoice": True, "manga_ocr": False, "bge_m3": False, "lama_manga": False, "vocal_separation_fv4": False, "nemotron_diarization": False, "pyannote_diarization": False,
        "whisper_large_v3": False, "whisper_large_v3_turbo": False, "whisper_ja_1_5b": False,
        "whisper_kotoba_v2": False, "whisper_medium": False, "whisper_anime": False,
        "whisper_breeze_asr_25": False, "whisper_thonburian": False}
    assert body["models"][0]["job_id"] == "model_download_qwen3_asr"


def test_a_check_that_cannot_tell_counts_as_not_downloaded(client, monkeypatch):
    def boom():
        raise OSError("disk gone")
    monkeypatch.setitem(svc.CATALOG, "sensevoice", {**svc.CATALOG["sensevoice"], "ready": boom})
    models = client.get("/api/diagnostics/model-downloads").json()["models"]
    assert [m["downloaded"] for m in models if m["id"] == "sensevoice"] == [False]


def test_start_downloads_in_the_background_and_then_reads_downloaded(client, models):
    state, fetched = models
    r = client.post("/api/diagnostics/model-downloads/manga_ocr/start", headers=PC)
    assert r.status_code == 200 and r.json() == {"job_id": "model_download_manga_ocr", "started": True}
    job = wait_for_job("model_download_manga_ocr")
    assert job["status"] == "done" and fetched == ["manga_ocr"]
    assert background_jobs.get_status("model_download_manga_ocr")["result"] == {"status": "ok"}
    assert client.post("/api/diagnostics/model-downloads/manga_ocr/start", headers=PC).status_code == 409


def test_unknown_model_is_refused(client):
    assert client.post("/api/diagnostics/model-downloads/whisper_large/start", headers=PC).status_code == 422
    assert client.post("/api/diagnostics/model-downloads/Bad-Name/start", headers=PC).status_code == 422


def test_second_download_waits_for_the_running_one(client, models, monkeypatch):
    monkeypatch.setattr(svc, "_running_other_than", lambda mid: "Qwen3-ASR and its aligner")
    r = client.post("/api/diagnostics/model-downloads/sensevoice/start", headers=PC)
    assert r.status_code == 409 and "Qwen3-ASR" in r.json()["error"]["message"]


def test_a_failed_download_stores_a_redacted_reason_without_paths(client, models, monkeypatch):
    def fail(report, cancelled):
        raise RuntimeError("HTTP 500 for /home/someone/private/cache with sk-ant-api03-SECRETSECRETSECRET123456")
    monkeypatch.setitem(svc.CATALOG, "qwen3_asr", {**svc.CATALOG["qwen3_asr"], "fetch": fail})
    client.post("/api/diagnostics/model-downloads/qwen3_asr/start", headers=PC)
    job = wait_for_job("model_download_qwen3_asr")
    assert job["status"] == "error"
    detail = background_jobs.get_status("model_download_qwen3_asr")["result"]["detail"]
    assert detail.startswith("Couldn't download Qwen3-ASR")
    assert "SECRET" not in detail and "/home/someone" not in detail


def test_a_download_that_leaves_the_files_incomplete_fails(client, models, monkeypatch):
    monkeypatch.setitem(svc.CATALOG, "manga_ocr", {**svc.CATALOG["manga_ocr"],
                                                   "fetch": lambda report, cancelled: None})
    client.post("/api/diagnostics/model-downloads/manga_ocr/start", headers=PC)
    assert wait_for_job("model_download_manga_ocr")["status"] == "error"
    detail = background_jobs.get_status("model_download_manga_ocr")["result"]["detail"]
    assert "incomplete" in detail


def test_missing_package_gives_a_plain_instruction(monkeypatch):
    import builtins
    real = builtins.__import__

    def no_modelscope(name, *a, **k):
        if name.startswith("modelscope"):
            raise ImportError(name)
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_modelscope)
    with pytest.raises(svc.ModelDownloadFailed, match="Packages and installs"):
        svc._fetch_modelscope(lambda f, m: None, lambda: False)


def test_hub_fetch_downloads_each_repo_at_its_pinned_revision(monkeypatch):
    hub = pytest.importorskip("huggingface_hub")
    calls = []
    monkeypatch.setattr(hub, "snapshot_download",
                        lambda repo, **kw: calls.append((repo, kw["revision"], kw["etag_timeout"])))
    messages = []
    svc._fetch_hub(["kha-white/manga-ocr-base", "Qwen/Qwen3-ForcedAligner-0.6B-hf"],
                   lambda f, m: messages.append((f, m)), lambda: False)
    assert [(c[0], c[1]) for c in calls] == [
        ("kha-white/manga-ocr-base", "aa6573bd10b0d446cbf622e29c3e084914df9741"),
        ("Qwen/Qwen3-ForcedAligner-0.6B-hf", "c07281df297b9905d24a508279258cccf987a064")]
    assert messages[0][0] == 0.0 and messages[1][0] == 0.5


def test_hub_progress_bar_reports_bytes_and_survives_a_disabled_bar(monkeypatch):
    hub = pytest.importorskip("huggingface_hub")
    bars = []
    monkeypatch.setattr(hub, "snapshot_download",
                        lambda repo, **kw: bars.append(kw["tqdm_class"]))
    messages = []
    svc._fetch_hub(["kha-white/manga-ocr-base"], lambda f, m: messages.append(m), lambda: False)
    bar = bars[0]
    # A disabled bar skips tqdm's init, so it has no `unit`; the library creates
    # one when progress bars are switched off.
    bar(total=2, unit="B", disable=True).update(1)
    bar(total=2, unit="it").update(1)
    bar(total=2_000_000_000, unit="B").update(1_500_000_000)
    assert messages[-1] == "Downloading kha-white/manga-ocr-base… 1.5 GB so far"


def test_start_is_pc_only_and_reading_needs_admin(models):
    app = create_app(ApiSettings(auth_mode="on"))
    remote = TestClient(app, base_url=REMOTE, raise_server_exceptions=False)
    url = "/api/diagnostics/model-downloads"
    assert remote.get(url).status_code == 401
    u = auth_service.add_user("kid@example.com")
    kid = auth_service.create_session(u["id"])
    assert remote.get(url, headers=session_headers(kid)).status_code == 403
    admin = _admin_session()
    assert remote.get(url, headers=session_headers(admin)).status_code == 200
    start = url + "/manga_ocr/start"
    assert remote.post(start, headers={**PC, **session_headers(admin)}).status_code == 403
    local = TestClient(app, raise_server_exceptions=False, **LOCAL)
    assert local.post(start, headers={**PC, "X-Forwarded-For": "1.2.3.4"}).status_code == 403
    assert models[1] == []
    assert local.post(start, headers=PC).status_code == 200
    wait_for_job("model_download_manga_ocr")


def _admin_session():
    u = auth_service.grant_admin_local("admin@example.com")
    return auth_service.create_session(u["id"])


def test_a_whisper_download_fetches_the_pinned_model_with_the_saved_token(monkeypatch):
    import whisper_models
    from services import settings_service
    calls = []
    monkeypatch.setattr(settings_service, "resolve_key", lambda key: "tok" if key == "hf_token" else None)
    monkeypatch.setattr(whisper_models, "_fetch_pinned_model", lambda size, token: calls.append(("pinned", size, token)))
    monkeypatch.setattr(whisper_models, "_fetch_anime_whisper", lambda token: calls.append(("anime", token)))
    monkeypatch.setattr(whisper_models, "_download_model", lambda size, token: calls.append(("generic", size, token)))
    svc._fetch_whisper("breeze-asr-25")(lambda *a: None, lambda: False)
    svc._fetch_whisper("large-v3")(lambda *a: None, lambda: False)
    svc._fetch_whisper("anime-whisper")(lambda *a: None, lambda: False)
    assert calls == [("pinned", "breeze-asr-25", "tok"), ("generic", "large-v3", "tok"), ("anime", "tok")]


def test_nemotron_download_fetches_only_the_files_the_loader_reads(monkeypatch):
    calls = []
    monkeypatch.setattr(svc, "_fetch_hub", lambda repos, report, cancelled, allow_patterns=None: calls.append((repos, allow_patterns)))
    svc._fetch_nemotron(lambda *a: None, lambda: False)
    assert calls == [(["nvidia/Nemotron-3-Diarization"], ["config.json", "processor_config.json", "model.safetensors"])]


def test_pyannote_download_uses_the_saved_token_and_explains_a_refusal(monkeypatch):
    import types
    from services import settings_service
    huggingface_hub = types.ModuleType("huggingface_hub")
    monkeypatch.setitem(sys.modules, "huggingface_hub", huggingface_hub)
    monkeypatch.setattr(settings_service, "resolve_key", lambda key: "tok")
    calls = []
    huggingface_hub.snapshot_download = lambda repo, **kw: calls.append((repo, kw["token"]))
    svc._fetch_pyannote(lambda *a: None, lambda: False)
    assert calls == [("pyannote/speaker-diarization-community-1", "tok")]

    class GatedRepoError(Exception):
        pass

    def refuse(repo, **kw):
        raise GatedRepoError("401 for a token that is not allowed")
    huggingface_hub.snapshot_download = refuse
    with pytest.raises(svc.ModelDownloadFailed) as err:
        svc._fetch_pyannote(lambda *a: None, lambda: False)
    assert "accept the terms at https://huggingface.co/pyannote/speaker-diarization-community-1" in str(err.value)
    assert "tok" not in str(err.value).replace("token", "")


def test_pyannote_download_without_a_token_says_where_to_add_one(monkeypatch):
    from services import settings_service
    monkeypatch.setattr(settings_service, "resolve_key", lambda key: None)
    with pytest.raises(svc.ModelDownloadFailed, match="Hugging Face token"):
        svc._fetch_pyannote(lambda *a: None, lambda: False)


def _fake_child(monkeypatch, tmp_path, script):
    """Runs `script` (a python snippet) in place of the ModelScope download child."""
    import types
    for name in ("modelscope", "modelscope.hub"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "modelscope.hub.snapshot_download", types.ModuleType("modelscope.hub.snapshot_download"))
    monkeypatch.setenv("MODELSCOPE_CACHE", str(tmp_path))
    monkeypatch.setattr(svc, "_MODELSCOPE_CHILD", script)
    monkeypatch.setattr(svc, "_POLL_SECONDS", 0.05)


def test_sensevoice_download_reports_bytes_from_the_cache(monkeypatch, tmp_path):
    folder = tmp_path / "models" / "iic" / "SenseVoiceSmall"
    _fake_child(monkeypatch, tmp_path, (
        "import os, time\n"
        f"os.makedirs({str(folder)!r}, exist_ok=True)\n"
        f"open({str(folder / 'model.pt')!r}, 'wb').write(b'x' * 5000000)\n"
        "time.sleep(1)"))
    seen = []
    svc._fetch_modelscope(lambda f, m: seen.append(m), lambda: False)
    assert any("5 MB so far" in m for m in seen)


def test_sensevoice_download_that_receives_nothing_is_stopped_with_a_plain_reason(monkeypatch, tmp_path):
    _fake_child(monkeypatch, tmp_path, "import time; time.sleep(30)")
    monkeypatch.setattr(svc, "_STALL_SECONDS", 0.2)
    started = time.monotonic()
    with pytest.raises(svc.ModelDownloadFailed, match="ModelScope"):
        svc._fetch_modelscope(lambda f, m: None, lambda: False)
    assert time.monotonic() - started < 15   # the child was killed, not waited for


def test_sensevoice_download_cancel_stops_the_child(monkeypatch, tmp_path):
    _fake_child(monkeypatch, tmp_path, "import time; time.sleep(30)")
    started = time.monotonic()
    with pytest.raises(background_jobs.JobCancelled):
        svc._fetch_modelscope(lambda f, m: None, lambda: True)
    assert time.monotonic() - started < 15


def test_bge_m3_is_ready_once_ollama_lists_it_and_a_missing_server_counts_as_not_ready(monkeypatch):
    from engine_backends import ollama_server
    monkeypatch.setattr(ollama_server, "_tag_entries", lambda url, timeout=5: [{"name": "bge-m3:latest"}])
    assert ollama_server.model_pulled("http://o:1", "bge-m3")

    def down(url, timeout=5):
        raise OSError("refused")
    monkeypatch.setattr(ollama_server, "_tag_entries", down)
    assert not ollama_server.model_pulled("http://o:1", "bge-m3")


def test_pulling_bge_m3_says_plainly_when_ollama_is_down_or_refuses(monkeypatch):
    from engine_backends import ollama_server
    monkeypatch.setattr(ollama_server, "check_ollama_reachable", lambda url: False)
    with pytest.raises(svc.ModelDownloadFailed, match="isn't running"):
        svc._fetch_bge_m3(lambda *a: None, lambda: False)

    monkeypatch.setattr(ollama_server, "check_ollama_reachable", lambda url: True)

    def refuse(url, model, progress=None, cancelled=None):
        raise ValueError("pull model manifest: file does not exist")
    monkeypatch.setattr(ollama_server, "pull_model", refuse)
    with pytest.raises(svc.ModelDownloadFailed, match="could not pull bge-m3"):
        svc._fetch_bge_m3(lambda *a: None, lambda: False)


class _StreamResp:
    ok, status_code = True, 200

    def __init__(self, lines, hold=None):
        self._lines, self._hold, self.closed = lines, hold, False

    def iter_lines(self):
        yield from self._lines
        if self._hold:
            self._hold.wait(10)

    def close(self):
        self.closed = True
        if self._hold:
            self._hold.set()


def _fake_session(monkeypatch, resp, sent=None, before_reply=None):
    from lib import http

    class Session:
        def close(self):
            pass

        def post(self, url, **kw):
            if before_reply:
                before_reply()
            if sent is not None:
                sent.update(url=url, json=kw["json"])
            return resp
    monkeypatch.setattr(http, "session", lambda **kw: Session())


def test_pull_model_streams_progress_across_layers(monkeypatch):
    import json
    from engine_backends import ollama_server
    resp = _StreamResp([json.dumps(e).encode() for e in (
        {"status": "pulling manifest"},
        {"digest": "a", "total": 1000, "completed": 250},
        {"digest": "b", "total": 1000, "completed": 0},
        {"digest": "a", "total": 1000, "completed": 1000},
        {"status": "success"})])
    sent, seen = {}, []
    _fake_session(monkeypatch, resp, sent)
    assert ollama_server.pull_model("http://o:1", "bge-m3", lambda d, t: seen.append((d, t))) is True
    assert sent["url"].endswith("/api/pull") and sent["json"] == {"model": "bge-m3", "stream": True}
    assert seen == [(250, 1000), (250, 2000), (1000, 2000)]
    assert resp.closed


def test_pull_model_raises_the_servers_error_without_paths(monkeypatch):
    import json
    from engine_backends import ollama_server
    _fake_session(monkeypatch, _StreamResp([json.dumps(
        {"error": "write /usr/share/ollama/blobs/x: no space left"}).encode()]))
    with pytest.raises(ValueError, match="no space left") as caught:
        ollama_server.pull_model("http://o:1", "bge-m3")
    assert "/usr/share" not in str(caught.value)


def test_pull_model_cancel_closes_the_connection_while_the_server_is_quiet(monkeypatch):
    import threading
    from engine_backends import ollama_server
    resp = _StreamResp([], hold=threading.Event())
    _fake_session(monkeypatch, resp)
    started = time.monotonic()
    assert ollama_server.pull_model("http://o:1", "bge-m3", None, lambda: True) is False
    assert time.monotonic() - started < 3 and resp.closed


def test_cancelling_the_bge_m3_download_ends_the_job_as_cancelled(monkeypatch):
    from engine_backends import ollama_server
    monkeypatch.setattr(ollama_server, "check_ollama_reachable", lambda url: True)
    monkeypatch.setattr(ollama_server, "pull_model", lambda url, model, progress, cancelled: False)
    with pytest.raises(background_jobs.JobCancelled):
        svc._fetch_bge_m3(lambda *a: None, lambda: True)


def test_pull_model_cancelled_before_ollama_answers_closes_the_late_response(monkeypatch):
    import threading
    from engine_backends import ollama_server
    asked, release, answered = threading.Event(), threading.Event(), threading.Event()
    resp = _StreamResp([b'{"digest": "a", "total": 10, "completed": 1}'])
    seen = []

    def wait_for_cancel():
        asked.set()
        release.wait(10)

    _fake_session(monkeypatch, resp, before_reply=wait_for_cancel)
    real_close = resp.close

    def close():
        real_close()
        answered.set()
    resp.close = close
    # Cancel only once the request is in flight, so no response exists yet when it is seen.
    cancel = lambda: asked.is_set()  # noqa: E731
    assert ollama_server.pull_model("http://o:1", "bge-m3", seen.append, cancel) is False
    release.set()
    assert answered.wait(5), "the late response was left open, so Ollama would keep pulling"
    assert seen == []


def test_pull_model_reports_the_reason_in_an_error_status(monkeypatch):
    from engine_backends import ollama_server

    class Refused(_StreamResp):
        ok, status_code = False, 404
        content = b'{"error": "pull model manifest: file does not exist"}'
    _fake_session(monkeypatch, Refused([]))
    with pytest.raises(ValueError, match="file does not exist"):
        ollama_server.pull_model("http://o:1", "bge-m3")


def test_the_fv4_download_fetches_that_model(monkeypatch):
    import audio_preprocess
    asked = []
    monkeypatch.setattr(audio_preprocess, "download_separation_model",
                        lambda model, cancel_check_cb=None: asked.append(model))
    svc.CATALOG["vocal_separation_fv4"]["fetch"](lambda f, m: None, lambda: False)
    assert asked == [audio_preprocess.MEL_ROFORMER_FV4_MODEL]
