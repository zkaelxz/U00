"""
Routes for translation arms and the translation settings sweep (api/routers/benchmark_routes.py):
who may call what, the confirm gates, request strictness, the options a run request carries, and
that no key leaves the server. The services' own behaviour is in test_benchmark_scenes.py and
test_benchmark_translate_sweep.py.
"""
import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import background_jobs
import db
import translate_engines
from services import benchmark_lab_service as svc
from services import translate_service
from tests.test_api_benchmark import BASE, LOCAL_HDR, SECRET, _admin, _error, _household, _local, _remote, _wait
from tests.test_benchmark_scenes import SceneEngine


@pytest.fixture(autouse=True)
def _clean_jobs():
    background_jobs.clear_all_jobs()
    yield
    background_jobs.clear_all_jobs()


def _scene(title="T", n=4, set_name="s"):
    return db.create_benchmark_clip_cases([
        {"label": f"{title}{i}", "stage": "translation", "content_type": "audio_drama", "source_language": "zh",
         "source_text": f"{title}{i}", "reference_text": f"EN {title}{i}", "tier": "application",
         "set_name": set_name, "title": title, "scene_order": i} for i in range(1, n + 1)])


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setitem(translate_engines.ENGINES, "claude", SceneEngine)
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, env_path=None: SECRET)
    SceneEngine.batches, SceneEngine.reverse = [], False


class TestTranslationArms:
    def test_options_list_what_the_form_offers(self, isolated_db):
        choices = _local().get(f"{BASE}/options").json()["translate_choices"]
        assert {p["key"] for p in choices["style_presets"]} >= {"audio_drama", "novel"}
        assert choices["defaults"] == {"context_window": 10, "context_window_ahead": 6,
                                       "batch_size": 30, "reflect": False, "style_preset": "audio_drama",
                                       "memory_examples": False}
        assert choices["temperature_engines"] == ["deepseek", "gemini", "ollama"]
        assert choices["batch_size_defaults"]["claude"][""] > 0

    def test_a_run_with_options_translates_a_title_as_batches(self, isolated_db, engine):
        _scene()
        body = {"stage": "translation", "confirm": True,
                "configs": [{"engine": "claude", "options": {"batch_size": 2, "context_window": 1}}]}
        r = _local().post(f"{BASE}/runs", json=body)
        assert r.status_code == 200, r.text
        _wait()
        assert [b["lines"] for b in SceneEngine.batches] == [["T1", "T2"], ["T3", "T4"]]
        run = _local().get(f"{BASE}/runs/{r.json()['session_ids'][0]}").json()["run"]
        assert run["context_settings"]["translate_options"]["context_window"] == 1
        assert SECRET not in json.dumps(run)

    def test_the_estimate_prices_the_options(self, isolated_db, engine):
        _scene()
        est = lambda o: _local().post(f"{BASE}/estimate", json={
            "stage": "translation", "configs": [{"engine": "claude", "options": o}]}).json()
        assert est({"reflect": True})["estimated_cost_usd"] == pytest.approx(
            est({"reflect": False})["estimated_cost_usd"] * 3)

    @pytest.mark.parametrize("options", [{"batch_size": 0}, {"temperature": 0.3}, {"series_id": 999},
                                         {"reflect": "yes"}, {"style_preset": "nope"}])
    def test_bad_options_are_refused(self, isolated_db, engine, options):
        _scene()
        r = _local().post(f"{BASE}/estimate", json={
            "stage": "translation", "configs": [{"engine": "claude", "options": options}]})
        assert r.status_code in (400, 422), r.text
        assert db.list_benchmark_sessions(10, "translation") == []


class TestTranslateSweepRoutes:
    def test_reads_need_diagnostics(self, isolated_db):
        hh, adm = _household(), _admin()
        for path in ("/translate-sweep", "/translate-sweep/decisions"):
            assert _local("off").get(BASE + path).status_code == 200, path
            assert _remote().get(BASE + path, headers=hh).status_code == 403, path
            assert _remote().get(BASE + path, headers=adm).status_code == 200, path
        assert _local("off").get(f"{BASE}/translate-sweep").json() == {"sweep": None}

    def test_start_and_decide_are_pc_only_and_need_confirm(self, isolated_db):
        body = {"set_name": "s", "language": "zh", "engine": "claude", "confirm": True}
        c, h = _remote(), _admin()
        for path, b in (("/translate-sweep", body),
                        ("/translate-sweep/decision", {"decision": "reject", "confirm": True})):
            r = c.post(BASE + path, headers={**h, **LOCAL_HDR}, json=b)
            assert r.status_code == 403, (path, r.text)
        assert background_jobs.get_status(svc.JOB_ID) is None
        r = _local().post(f"{BASE}/translate-sweep", json={**body, "confirm": False})
        assert r.status_code == 422 and "Confirmation" in _error(r)["message"]

    def test_estimate_refuses_a_set_with_no_cases(self, isolated_db):
        r = _local().post(f"{BASE}/translate-sweep/estimate",
                          json={"set_name": "s", "language": "zh", "engine": "claude"})
        assert r.status_code == 400 and "no translation cases" in _error(r)["message"]

    def test_the_request_is_strict(self, isolated_db):
        base = {"set_name": "s", "language": "zh", "engine": "claude"}
        for bad in ({**base, "language": "fr"}, {**base, "extra": 1}, {**base, "series_id": 0},
                    {"set_name": "s", "language": "zh"}):
            assert _local().post(f"{BASE}/translate-sweep/estimate", json=bad).status_code == 422, bad

    def test_an_estimate_prices_a_paid_engine_and_leaks_no_key(self, isolated_db, engine):
        for title in "ABCD":
            _scene(title, 3)
        r = _local().post(f"{BASE}/translate-sweep/estimate",
                          json={"set_name": "s", "language": "zh", "engine": "claude"})
        assert r.status_code == 200, r.text
        assert r.json()["estimated_cost_usd"] > 0 and SECRET not in r.text
        assert db.list_benchmark_sessions(10, "translation") == []
