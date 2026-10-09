"""Run settings shown in the Jobs Details panel: allow-list only, scalars only."""
import json

import background_jobs
from services import jobs_service, run_settings_service

SECRETS = [
    "sk-ant-ABCDEFGHIJKLMNOP1234567890", "AIzaSyA-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
    "hf_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
]


class TestSanitise:
    def test_keeps_allowed_scalars(self):
        raw = {"engine": "deepseek", "model": "deepseek-chat", "whisper_size": "large-v3",
               "beam_size": 5, "segment_seconds": 20.5, "use_gpu": True, "glossary": False}
        assert run_settings_service.sanitise(raw) == raw

    def test_drops_keys_not_on_the_allow_list(self):
        raw = {"beam_size": 5, "audio_path": "/home/me/a.wav", "api_key": "x", "prompt": "hi",
               "url": "https://example.com/stream", "initial_prompt": "secret names"}
        assert run_settings_service.sanitise(raw) == {"beam_size": 5}

    def test_drops_paths_urls_and_free_text_even_under_an_allowed_key(self):
        for bad in ("/home/me/models/large-v3", "C:\\Users\\me\\model", "../model", "~/m",
                    "https://host/model", "host:/x//y", "two words", "a" * 80, ""):
            assert run_settings_service.sanitise({"model": bad}) == {}, bad

    def test_drops_key_like_strings(self):
        for secret in SECRETS:
            assert run_settings_service.sanitise({"model": secret, "engine": secret}) == {}, secret

    def test_wrong_kinds_are_dropped_not_coerced(self):
        raw = {"beam_size": "5", "use_gpu": 1, "glossary": "yes", "model": 3, "batch_size": True,
               "segment_seconds": float("nan"), "context_window": 2.5, "engine": ["a"]}
        assert run_settings_service.sanitise(raw) == {}

    def test_non_dict_is_empty(self):
        assert run_settings_service.sanitise(None) == {}
        assert run_settings_service.sanitise(["engine"]) == {}


class TestBuild:
    def test_later_sources_and_keywords_override(self):
        assert run_settings_service.build(
            {"beam_size": 5, "use_gpu": False}, use_gpu=True) == {"beam_size": 5, "use_gpu": True}

    def test_a_run_with_nothing_allowed_is_empty(self):
        assert run_settings_service.build(audio_path="/x") == {}

    def test_ollama_registry_host_is_dropped_from_the_model(self):
        assert run_settings_service.sanitise({"model": "nas.lan:5000/me/qwen:7b"}) == {
            "model": "qwen:7b"}
        assert run_settings_service.sanitise({"model": "qwen2.5:7b"}) == {"model": "qwen2.5:7b"}
        for path in ("/home/me/m.bin", "C:/models/m.bin", "C:\\models\\m.bin", "a/../b/m.bin"):
            assert run_settings_service.sanitise({"model": path}) == {}, path


class TestSettingsLiveOnTheJobEntry:
    def _wait(self, job_id):
        import time
        deadline = time.time() + 3
        while (background_jobs.get_status(job_id) or {}).get("status") in (None, "running", "queued"):
            assert time.time() < deadline
            time.sleep(0.01)

    def _stored(self, isolated_db, job_id):
        return json.loads(isolated_db.get_job_record(job_id)["result_json"] or "null")

    def test_a_later_run_without_settings_never_inherits_the_earlier_ones(self, isolated_db):
        background_jobs.clear_all_jobs()
        background_jobs.start_job("rs_reuse", lambda: None, run_settings={"beam_size": 5})
        self._wait("rs_reuse")
        background_jobs.start_job("rs_reuse", lambda: None)
        self._wait("rs_reuse")
        assert self._stored(isolated_db, "rs_reuse") is None

    def test_a_refused_start_leaves_the_live_jobs_settings_alone(self, isolated_db):
        import threading
        background_jobs.clear_all_jobs()
        gate = threading.Event()
        assert background_jobs.start_job("rs_live", gate.wait, run_settings={"beam_size": 5})
        assert not background_jobs.start_job("rs_live", lambda: None, run_settings={"beam_size": 9})
        gate.set()
        self._wait("rs_live")
        assert self._stored(isolated_db, "rs_live") == {"run_settings": {"beam_size": 5}}

    def test_the_first_mirror_already_carries_this_runs_settings(self, isolated_db):
        import threading
        background_jobs.clear_all_jobs()
        gate = threading.Event()
        background_jobs.start_job("rs_first", gate.wait, run_settings={"beam_size": 5})
        gate.set()
        self._wait("rs_first")
        background_jobs.start_job("rs_first", gate.wait, run_settings={"beam_size": 7})
        assert self._stored(isolated_db, "rs_first") == {"run_settings": {"beam_size": 7}}

    def test_unsanitised_settings_are_cleaned_on_the_way_in(self, isolated_db):
        background_jobs.clear_all_jobs()
        background_jobs.start_job("rs_dirty", lambda: None,
                                  run_settings={"beam_size": 5, "audio_path": "/home/me/a.wav"})
        self._wait("rs_dirty")
        assert self._stored(isolated_db, "rs_dirty") == {"run_settings": {"beam_size": 5}}

    def test_a_queued_process_job_keeps_its_settings_when_promoted(self, isolated_db, monkeypatch):
        background_jobs.clear_all_jobs()
        monkeypatch.setattr(background_jobs, "get_gpu_limit_enabled", lambda: True)
        monkeypatch.setattr(background_jobs, "_gpu_slot_available_locked", lambda *a, **k: False)
        monkeypatch.setattr(background_jobs, "_gpu_queue_waiting_locked", lambda: False)
        assert background_jobs.start_process_job(
            "rs_queued", print, gpu_touching=True, run_settings={"beam_size": 5})
        assert self._stored(isolated_db, "rs_queued") == {"run_settings": {"beam_size": 5}}
        entry = background_jobs._jobs["rs_queued"]
        assert entry["status"] == "queued" and entry["run_settings"] == {"beam_size": 5}
        proc, _q = background_jobs._register_process_job(
            "rs_queued", print, (), True, None, None, run_settings=entry["run_settings"])
        assert background_jobs._jobs["rs_queued"]["run_settings"] == {"beam_size": 5}
        background_jobs.clear_all_jobs()


class TestProjection:
    def test_project_result_resanitises_stored_run_settings(self):
        result = jobs_service.project_result({
            "line_count": 3,
            "run_settings": {"beam_size": 5, "model": SECRETS[0], "audio_path": "/a/b.wav",
                             "nested": {"x": 1}}})
        assert result["run_settings"] == {"beam_size": 5}

    def test_no_run_settings_key_when_none_survive(self):
        assert "run_settings" not in jobs_service.project_result(
            {"line_count": 3, "run_settings": {"audio_path": "/a"}})

    def test_project_result_json_merges_the_settings(self):
        stored = json.loads(jobs_service.project_result_json(
            {"line_count": 2}, {"engine": "claude", "glossary": True}))
        assert stored == {"line_count": 2, "run_settings": {"engine": "claude", "glossary": True}}

    def test_a_cue_list_result_still_carries_the_settings(self):
        assert json.loads(jobs_service.project_result_json(
            [{"text": "hi"}], {"whisper_size": "small"})) == {
            "run_settings": {"whisper_size": "small"}}
        assert jobs_service.project_result_json([{"text": "hi"}]) is None

    def test_job_details_expose_only_the_allow_listed_settings(self, isolated_db):
        import time
        background_jobs.start_job(
            "rs_job", lambda: background_jobs.set_result("rs_job", {"line_count": 1}),
            run_settings=run_settings_service.build(
                {"audio_path": "/home/me/a.wav", "beam_size": 5}, asr_backend="whisper",
                model="/home/me/m.bin"))
        deadline = time.time() + 3
        while (background_jobs.get_status("rs_job") or {}).get("status") != "done":
            assert time.time() < deadline
            time.sleep(0.01)
        job = jobs_service.get_job("rs_job")
        assert job["result"] == {"line_count": 1,
                                 "run_settings": {"asr_backend": "whisper", "beam_size": 5}}
        assert "/home/me" not in json.dumps(job)
