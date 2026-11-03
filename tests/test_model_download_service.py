"""The Diagnostics model download: what can be fetched, who may start it, and how a
job ends. The Hub and ModelScope fetches are replaced; no network, no model."""
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
        "qwen3_asr": False, "sensevoice": True, "manga_ocr": False}
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
