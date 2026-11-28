"""Tests for Reflect and bulk modes of start_translate_run +
POST /api/translate-run/dramas/{id}/run (Migration Slice 41). Fully mocked:
a fake batch provider, a fake Reflect helper, no network, no real keys."""
import json
import time

import pytest
from fastapi.testclient import TestClient

import background_jobs
from bulk import bulk_poll
from bulk import bulk_providers
from bulk import bulk_reflect
from bulk import bulk_translate
import db
from engine_backends import engine_registry
from engine_backends import claude as engine_claude
from api.server import create_app
from core import Line
from services import bulk_submit_service
from services import translate_run_service as svc
from services import translate_service
from tests.saved_settings import patch_setting
from lib.errors import (ConflictError, InvalidInputError,
                                      UnsupportedOperationError)
from tests.job_helpers import wait_for_job


@pytest.fixture(autouse=True)
def _reflect_runs_enabled(monkeypatch):
    # New Reflect runs are switched off in the app; these tests cover the pipeline a job submitted earlier still finishes through.
    monkeypatch.setattr(engine_registry, "REFLECT_RUNS_ENABLED", True)


def _seed(texts, engine="claude"):
    did = db.create_drama(title_zh="D", translation_engine=engine)
    db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=z, en=en)
                        for i, (z, en) in enumerate(texts)])
    return did


class FakeProvider:
    """Answers each request id-keyed, keys reversed so nothing lines up by
    position. ended=False keeps the batch pending (for the cancel test)."""
    submitted, cancelled, _jobs = [], [], {}  # shared: the job builds more than one
    fail = {}  # stage -> "all" | "first": requests of that stage that come back as errors
    on_results = {}  # stage -> callable run just before that stage's results arrive

    def __init__(self, drama_id, ended=True):
        self.drama_id, self.ended = drama_id, ended

    def build_request(self, key, context, numbered):
        return {"key": key}

    def build_prompt_request(self, key, prompt, max_tokens=3000):
        return {"key": key}

    def submit(self, requests_):
        job = next(j for j in db.list_bulk_jobs(self.drama_id) if j["status"] == "submitting")
        batch_id = f"batch_{len(self.submitted)}"
        self.submitted.append(requests_)
        self._jobs[batch_id] = job
        return batch_id

    def poll(self, batch_id):
        return "ended" if self.ended else "pending"

    def results(self, batch_id):
        job = self._jobs[batch_id]
        tag = job.get("stage") or "tr"
        by_key = {}
        for r in db.list_bulk_job_lines(job["id"]):
            by_key.setdefault(r["request_key"], []).append(r["line_id"])
        if tag in self.on_results:
            self.on_results[tag]()
        for n, (key, ids) in enumerate(reversed(list(by_key.items()))):
            if self.fail.get(tag) == "all" or (self.fail.get(tag) == "first" and n == 0):
                yield key, None, {"input_tokens": 0}, "expired"
                continue
            payload = {str(lid): f"{tag}-{lid}" for lid in reversed(ids)}
            yield key, json.dumps(payload), {"input_tokens": 0}, None

    def cancel(self, batch_id):
        self.cancelled.append(batch_id)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    background_jobs.clear_all_jobs()
    FakeProvider.submitted, FakeProvider.cancelled, FakeProvider._jobs = [], [], {}
    FakeProvider.fail, FakeProvider.on_results = {}, {}
    monkeypatch.setattr(bulk_poll, "POLL_INTERVAL_SECONDS", 0.02)
    # A batch or an off-peak run is refused without a monthly cap.
    monkeypatch.setattr(svc.settings_service, "get_monthly_cap_usd", lambda *a, **k: 50.0)
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name: "sk-fake")
    monkeypatch.setattr(svc, "pick_summary_engine", lambda *a, **k: (None, None))
    yield
    background_jobs.clear_all_jobs()


def _fake_engines(monkeypatch, ended=True):
    engine = engine_claude.ClaudeEngine("sk-ant-fake")
    monkeypatch.setattr(engine_registry, "get_engine", lambda *a, **k: engine)
    for module in (bulk_providers, bulk_translate, bulk_reflect, bulk_poll):
        monkeypatch.setattr(module, "make_provider",
                            lambda choice, e: FakeProvider(_current["did"], ended=ended))
    return engine


_current = {}


def test_bulk_translation_applies_by_line_id_and_keeps_hand_edits(isolated_db, monkeypatch):
    did = _seed([("一", ""), ("二", "My edit"), ("三", "")])
    _current["did"] = did
    _fake_engines(monkeypatch)
    out = svc.start_translate_run(did, bulk=True)
    assert out["job_id"] == f"bulk_translate_{did}" and out["bulk"] and out["target_line_count"] == 2
    job = wait_for_job(out["job_id"])
    assert job["status"] == "done", job
    assert job["result"]["status"] == "applied"
    rows = {r["zh"]: r for r in db.load_lines(did)}
    assert rows["一"]["en"] == f"tr-{rows['一']['id']}"
    assert rows["三"]["en"] == f"tr-{rows['三']['id']}"
    assert rows["二"]["en"] == "My edit"


def test_bulk_reflect_follows_all_three_stages_inside_the_job(isolated_db, monkeypatch):
    did = _seed([("一", ""), ("二", "")])
    _current["did"] = did
    _fake_engines(monkeypatch)
    out = svc.start_translate_run(did, bulk=True, reflect=True)
    job = wait_for_job(out["job_id"])
    assert job["status"] == "done", job
    assert job["result"]["stage"] == "expressive" and job["result"]["status"] == "applied"
    stages = sorted(j["stage"] for j in db.list_bulk_jobs(did))
    assert stages == ["expressive", "faithful", "reflect"]
    for r in db.load_lines(did):
        assert r["en"] == f"expressive-{r['id']}"


def _run_reflect(did, monkeypatch, **kw):
    _current["did"] = did
    _fake_engines(monkeypatch)
    return wait_for_job(svc.start_translate_run(did, bulk=True, reflect=True, **kw)["job_id"])


def test_reflect_stage_that_fully_fails_still_writes_the_paid_drafts(isolated_db, monkeypatch):
    did = _seed([("一", ""), ("二", ""), ("三", "")])
    ids = {r["zh"]: r["id"] for r in db.load_lines(did)}
    FakeProvider.fail = {"reflect": "all"}
    FakeProvider.on_results = {"reflect": lambda: db.save_lines(
        did, [ln for ln in db.load_line_objects(did) if setattr(ln, "en", "Mine" if ln.zh == "二" else ln.en) or True],
        fields=("en",))}
    job = _run_reflect(did, monkeypatch)
    assert job["status"] == "done", job
    rows = {r["zh"]: r["en"] for r in db.load_lines(did)}
    assert rows == {"一": f"faithful-{ids['一']}", "二": "Mine", "三": f"faithful-{ids['三']}"}
    assert sorted(j["stage"] for j in db.list_bulk_jobs(did)) == ["faithful", "reflect"]
    assert len(FakeProvider.submitted) == 2  # no stage was sent again


def test_reflect_stage_that_partly_fails_writes_drafts_only_for_the_failed_lines(isolated_db, monkeypatch):
    # 61 lines: a Claude Sonnet stage sends 60 per request, so two requests.
    did = _seed([(f"行{n}", "") for n in range(61)])
    FakeProvider.fail = {"reflect": "first"}  # the 1-line request
    job = _run_reflect(did, monkeypatch)
    assert job["status"] == "done", job
    kinds = [r["en"].split("-")[0] for r in db.load_lines(did)]
    assert sorted(set(kinds)) == ["expressive", "faithful"]
    assert kinds.count("faithful") == 1
    assert len(FakeProvider.submitted) == 3  # one batch per stage; nothing sent twice


def test_expressive_stage_that_fails_falls_back_to_the_paid_drafts(isolated_db, monkeypatch):
    did = _seed([("一", ""), ("二", "")])
    FakeProvider.fail = {"expressive": "all"}
    job = _run_reflect(did, monkeypatch)
    assert job["status"] == "done", job
    for r in db.load_lines(did):
        assert r["en"] == f"faithful-{r['id']}"
    assert len(FakeProvider.submitted) == 3


def test_cancel_cancels_the_pending_batch(isolated_db, monkeypatch):
    did = _seed([("一", "")])
    _current["did"] = did
    _fake_engines(monkeypatch, ended=False)
    out = svc.start_translate_run(did, bulk=True)
    for _ in range(200):
        if FakeProvider.submitted:
            break
        time.sleep(0.02)
    background_jobs.request_cancel(out["job_id"])
    wait_for_job(out["job_id"])
    bulk = db.list_bulk_jobs(did)[0]
    assert bulk["status"] == "cancelled"
    assert FakeProvider.cancelled == [bulk["provider_batch_id"]]
    assert db.load_lines(did)[0]["en"] == ""


def test_pending_bulk_job_is_a_conflict(isolated_db, monkeypatch):
    did = _seed([("一", "")])
    _current["did"] = did
    _fake_engines(monkeypatch)
    rows = db.load_lines(did)
    db.create_bulk_job(did, "claude", "m", "submitted", [(rows[0]["id"], "k", "h", "")])
    with pytest.raises(ConflictError):
        svc.start_translate_run(did, bulk=True)


def test_bulk_estimate_above_cap_is_refused(isolated_db, monkeypatch):
    did = _seed([("你好" * 50, "")])
    _current["did"] = did
    _fake_engines(monkeypatch)
    with pytest.raises(UnsupportedOperationError):
        svc.start_translate_run(did, bulk=True, job_cost_cap_usd=1e-9)
    assert db.list_bulk_jobs(did) == []


def test_the_bulk_cap_check_prices_the_runs_style(isolated_db, monkeypatch):
    did = _seed([("你好", "")])
    _current["did"] = did
    _fake_engines(monkeypatch)
    styles, real = [], svc.estimate_translate_cost
    monkeypatch.setattr(svc, "estimate_translate_cost",
                        lambda *a, **k: styles.append(k.get("style_preset")) or real(*a, **k))
    with pytest.raises(UnsupportedOperationError):
        svc.start_translate_run(did, bulk=True, style_preset="novel", job_cost_cap_usd=1e-9)
    assert styles == ["novel"]


def test_mode_validation(isolated_db, monkeypatch):
    patch_setting(monkeypatch, "gemini_free_tier", True)  # a request alone can't grant it
    did = _seed([("一", "")])
    _current["did"] = did
    _fake_engines(monkeypatch)
    with pytest.raises(InvalidInputError):
        svc.start_translate_run(did, reflect=True,
                                fallback_chain=[{"engine": "gemini", "model": None}])
    with pytest.raises(InvalidInputError):
        svc.start_translate_run(did, bulk=True, line_ids=[db.load_lines(did)[0]["id"]])
    with pytest.raises(UnsupportedOperationError):
        svc.start_translate_run(did, engine_name="fake_mt", reflect=True)
    with pytest.raises(UnsupportedOperationError):
        svc.start_translate_run(did, engine_name="ollama", bulk=True)
    with pytest.raises(UnsupportedOperationError):
        svc.start_translate_run(did, engine_name="gemini", bulk=True, gemini_free_tier=True)
    with pytest.raises(UnsupportedOperationError):
        svc.start_translate_run(did, engine_name="deepseek", bulk=True, reflect=True)


def test_reflect_live_run_uses_reflect_helper_and_saves_notes(isolated_db, monkeypatch):
    did = _seed([("你好", ""), ("再见", "keep")])
    seen = []

    def fake_reflect(engine, zh_lines, context, usage_cb=None, max_retries=1, pass_cb=None, resume=None):
        seen.append(list(context["line_ids"]))
        return [f"R:{z}" for z in zh_lines], ["crit" for _ in zh_lines]
    monkeypatch.setattr("engine_backends.translate_pipeline.reflect_translate_batch", fake_reflect)
    out = svc.start_translate_run(did, engine_name="fake", reflect=True)
    assert out["reflect"] and not out["bulk"] and out["job_id"] == f"translate_{did}"
    assert wait_for_job(out["job_id"])["status"] == "done"
    ens = {r["zh"]: r["en"] for r in db.load_lines(did)}
    assert ens == {"你好": "R:你好", "再见": "keep"}
    assert len(seen) == 1 and len(seen[0]) == 1
    assert any(n.get("note") == "crit" for n in db.list_translation_notes(did))


def test_api_reflect_and_bulk_codes(isolated_db, monkeypatch):
    did = _seed([("一", "")])
    _current["did"] = did
    _fake_engines(monkeypatch)
    client = TestClient(create_app(), headers={"X-Baihe-Local": "1"})
    r = client.post(f"/api/translate-run/dramas/{did}/run",
                    json={"engine": "fake_mt", "reflect": True})
    assert r.status_code == 400
    r = client.post(f"/api/translate-run/dramas/{did}/run",
                    json={"bulk": True, "fallback_chain": [{"engine": "gemini"}]})
    assert r.status_code == 422
    r = client.post(f"/api/translate-run/dramas/{did}/run", json={"bulk": True})
    assert r.status_code == 200 and r.json()["bulk"] is True
    assert wait_for_job(r.json()["job_id"])["status"] == "done"
    assert client.post("/api/translate-run/dramas/999/bulk/resume").status_code == 404


def test_resume_starts_poller_for_pending_job(isolated_db, monkeypatch):
    did = _seed([("一", "")])
    _current["did"] = did
    _fake_engines(monkeypatch, ended=False)
    rows = db.load_lines(did)
    bid = db.create_bulk_job(did, "claude", "m", "submitted", [(rows[0]["id"], "k", "h", "")])
    out = svc.resume_bulk_translations(did)
    assert out == {"drama_id": did, "jobs": [{"bulk_job_id": bid, "state": "polling"}]}
    bulk_poll.cancel_bulk_job(bid)


def test_resume_needs_engines_paid_for_a_paid_engines_job(isolated_db, monkeypatch):
    """A resumed job can send new paid work (a Reflect stage, an off-peak
    submit), so a member without engines.paid can't restart one."""
    from api import auth as api_auth
    from api.api_config import ApiSettings
    from services import auth_service
    did = _seed([("一", "")])
    _current["did"] = did
    _fake_engines(monkeypatch, ended=False)
    rows = db.load_lines(did)
    bid = db.create_bulk_job(did, "claude", "m", "needs_resubmit", [(rows[0]["id"], "k", "h", "")])
    started = []
    monkeypatch.setattr(bulk_poll, "start_poller", lambda job_id, **kw: started.append(job_id))
    u = auth_service.add_user("kid@example.com")
    s = auth_service.create_session(u["id"], "pytest", "203.0.113.9")
    client = TestClient(create_app(ApiSettings(auth_mode="on", serve_frontend=False),
                                   frontend_dist=None),
                        base_url="https://baihe.example.com", client=("203.0.113.9", 5000),
                        headers={"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
                                 api_auth.CSRF_HEADER: s["csrf_token"]})
    url = f"/api/translate-run/dramas/{did}/bulk/resume"
    assert client.post(url).status_code == 403
    assert started == []
    auth_service.grant_permission(u["id"], "engines.paid")
    r = client.post(url)
    assert r.status_code == 200 and r.json()["jobs"] == [{"bulk_job_id": bid, "state": "polling"}]
    assert started == [bid]


def test_offpeak_schedule_stores_the_runs_batching(isolated_db, monkeypatch):
    patch_setting(monkeypatch, "scene_aware_batches", True)
    did = _seed([("一", ""), ("二", "")], engine="deepseek")
    submit = bulk_submit_service.bulk_submitter(did, db.get_drama(did), type("E", (), {"model": "m"})(), "deepseek",
                                 False, None, [], "", "", "en-US", "audio_drama", 6, 2, 7, False,
                                 None, None, None)
    args = db.get_bulk_job(submit())["translate_args"]
    assert (args["batch_size"], args["context_window_ahead"], args["scene_aware_batches"]) == (7, 2, True)


def test_bulk_submission_gets_the_engines_default_batch_size(isolated_db, monkeypatch):
    did = _seed([("一", "")], engine="claude")
    sent = {}
    monkeypatch.setattr(svc.bulk_translate, "submit_bulk_translation",
                        lambda *a, **kw: sent.update(kw) or 1)
    engine = type("E", (), {"model": "m"})()
    for engine_name, model, expected in (("gemini", "gemini-pro-latest", 60), ("claude", "claude-sonnet-5-5", 30)):
        batch = engine_registry.default_batch_size(engine_name, model)
        bulk_submit_service.bulk_submitter(did, db.get_drama(did), engine, "claude", False, None, [], "", "",
                            "en-US", "audio_drama", 6, 2, batch, False, None, None, None)()
        assert sent["batch_size"] == expected


def _other_title_batch(estimated_usd, engine="claude", status="submitted"):
    other = _seed([("别", "")])
    db.create_bulk_job(other, engine, "m", status, [(db.load_lines(other)[0]["id"], "k", "h", "")],
                       estimated_usd=estimated_usd)
    return other


def test_another_titles_pending_batch_holds_its_share_of_the_cap(isolated_db, monkeypatch):
    # Cap $50: a batch on another title still waiting for results holds
    # $49.99 of it, so this one (whose own estimate fits $50) is refused.
    did = _seed([("你好" * 200, "")])
    _current["did"] = did
    _fake_engines(monkeypatch)
    _other_title_batch(49.99999)
    with pytest.raises(UnsupportedOperationError, match="pending bulk jobs"):
        svc.start_translate_run(did, bulk=True)
    assert db.list_bulk_jobs(did) == []
    from services import paid_job_service
    assert paid_job_service._bulk_holds == {}


def test_a_pending_batch_counts_against_a_normal_run(isolated_db, monkeypatch):
    # Cap $50, all of it held by another title's batch still waiting for
    # results: a live run would spend money the batch will be billed.
    did = _seed([("你好", "")])
    _current["did"] = did
    _fake_engines(monkeypatch)
    _other_title_batch(50.0)
    with pytest.raises(UnsupportedOperationError):
        svc.start_translate_run(did)
    assert not background_jobs.is_running(f"translate_{did}")


def test_an_offpeak_run_leaves_room_for_other_pending_batches(isolated_db):
    _other_title_batch(30.0)
    offpeak = _other_title_batch(100.0, engine="deepseek", status="scheduled")
    cap, refusal = bulk_providers.offpeak_cap(db.list_bulk_jobs(offpeak)[0], 50.0)
    # Its own row holds none of the cap: it stops at the cap by itself.
    assert refusal is None and cap == pytest.approx(20.0)
    _other_title_batch(20.0)
    assert bulk_providers.offpeak_cap(db.list_bulk_jobs(offpeak)[0], 50.0)[1]


def test_a_batchs_cost_is_logged_for_the_member_who_sent_it(isolated_db):
    did = _seed([("一", "")])
    lid = db.load_lines(did)[0]["id"]
    with db.spending_for(42):
        bid = db.create_bulk_job(did, "claude", "claude-sonnet-5-5", "submitted",
                                 [(lid, "k", bulk_translate.zh_hash("一"), "")],
                                 provider_batch_id="b", estimated_usd=1.0)
    assert db.get_user_month_spend(42) == pytest.approx(1.0)

    class Ended:
        def poll(self, batch_id):
            return "ended"

        def results(self, batch_id):
            yield "k", json.dumps({str(lid): "One"}), {"input_tokens": 1000, "output_tokens": 100}, None

    # Polled with no one signed in, as the background poller is.
    assert bulk_poll.check_once(bid, Ended()) == "applied"
    with db.get_conn() as conn:
        rows = conn.execute("SELECT user_id, estimated_cost_usd FROM usage_log").fetchall()
    assert [r[0] for r in rows] == [42]
    # Billed now, so the estimate no longer counts on top.
    assert db.get_user_month_spend(42) == pytest.approx(rows[0][1])



def test_a_members_unbilled_spend_counts_only_their_own_held_batches(isolated_db):
    # Read from the member's rows alone (the /me limits panel asks on every load),
    # it must still agree with the library-wide sum split by member.
    def batch(user_id, usd, engine="claude", status="submitted"):
        with db.spending_for(user_id):
            _other_title_batch(usd, engine=engine, status=status)
    batch(42, 1.0)
    batch(42, 0.5, status="applying")
    batch(42, 9.0, status="applied")
    batch(42, 7.0, engine="deepseek", status="scheduled")
    batch(43, 3.0)
    batch(None, 4.0)
    assert db.unbilled_bulk_usd_for_user(42) == pytest.approx(1.5)
    assert db.unbilled_bulk_usd_for_user(43) == pytest.approx(3.0)
    assert db.unbilled_bulk_usd_for_user(44) == 0.0
    by_user = db.unbilled_bulk_usd_by_user()
    for user_id in (42, 43):
        assert db.unbilled_bulk_usd_for_user(user_id) == pytest.approx(by_user[user_id])
    assert db.get_user_month_spend(42) == pytest.approx(1.5)

def test_a_batch_records_its_estimate_and_releases_its_hold(isolated_db, monkeypatch):
    did = _seed([("你好" * 200, "")])
    _current["did"] = did
    _fake_engines(monkeypatch)
    _other_title_batch(1.0)
    # A DeepSeek off-peak row stops at the cap by itself, so it holds none.
    _other_title_batch(100.0, engine="deepseek", status="scheduled")
    expected = svc.estimate_translate_cost(did, bulk=True)["estimated_usd"]
    job = wait_for_job(svc.start_translate_run(did, bulk=True)["job_id"])
    assert job["status"] == "done", job
    row = db.list_bulk_jobs(did)[0]
    assert row["estimated_usd"] == pytest.approx(expected) and expected > 0
    from services import paid_job_service
    assert paid_job_service._bulk_holds == {}


def test_a_batch_accepted_but_not_yet_recorded_holds_the_cap(isolated_db, monkeypatch):
    # Two titles sent close together: the first one's row doesn't exist yet
    # while its job builds the requests, but its estimate is already held.
    from services import paid_job_service
    did = _seed([("你好" * 200, "")])
    _current["did"] = did
    _fake_engines(monkeypatch)
    paid_job_service.hold_bulk_spend(paid_job_service.bulk_hold_key(999, "translate"), 49.99999)
    try:
        with pytest.raises(UnsupportedOperationError, match="other pending bulk jobs"):
            svc.start_translate_run(did, bulk=True)
    finally:
        paid_job_service.release_bulk_spend(paid_job_service.bulk_hold_key(999, "translate"))
    wait_for_job(svc.start_translate_run(did, bulk=True)["job_id"])
    assert db.list_bulk_jobs(did)[0]["status"] == "applied"


def test_a_held_batch_and_its_own_row_are_counted_once(isolated_db):
    from services import paid_job_service
    other = _other_title_batch(30.0)
    keys = [paid_job_service.bulk_hold_key(other, "reflect"), paid_job_service.bulk_hold_key(other, "flag")]
    paid_job_service.hold_bulk_spend(keys[0], 30.0)  # the same batch as the row
    paid_job_service.hold_bulk_spend(keys[1], 5.0)
    try:
        with paid_job_service._bulk_holds_lock:
            assert paid_job_service._held_usd() == pytest.approx(35.0)
        with pytest.raises(ConflictError):
            paid_job_service.hold_bulk_spend(keys[1], 1.0)
    finally:
        for key in keys:
            paid_job_service.release_bulk_spend(key)


def test_reflect_stages_carry_the_rest_of_the_pipelines_estimate(isolated_db, monkeypatch):
    did = _seed([("一", ""), ("二", "")])
    job = _run_reflect(did, monkeypatch)
    assert job["status"] == "done", job
    by_stage = {j["stage"]: j["estimated_usd"] for j in db.list_bulk_jobs(did)}
    assert by_stage["faithful"] > 0
    assert by_stage["reflect"] == pytest.approx(by_stage["faithful"] * 2 / 3)
    assert by_stage["expressive"] == pytest.approx(by_stage["faithful"] / 3)


def test_next_reflect_stage_is_not_sent_when_other_batches_hold_the_cap(isolated_db, monkeypatch):
    did = _seed([("一", ""), ("二", "")])
    ids = {r["zh"]: r["id"] for r in db.load_lines(did)}
    other = _seed([("别", "")])
    # Another title's batch is accepted while the first stage is out.
    FakeProvider.on_results = {"faithful": lambda: db.create_bulk_job(
        other, "claude", "m", "submitted", [], estimated_usd=49.99999)}
    job = _run_reflect(did, monkeypatch)
    assert job["status"] == "done", job
    rows = {r["zh"]: r["en"] for r in db.load_lines(did)}
    assert rows == {"一": f"faithful-{ids['一']}", "二": f"faithful-{ids['二']}"}
    faithful = next(j for j in db.list_bulk_jobs(did) if j["stage"] == "faithful")
    assert "other pending bulk jobs" in faithful["last_error"]
    assert len(FakeProvider.submitted) == 1
