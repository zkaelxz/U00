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


class TestRecord:
    def test_later_sources_and_keywords_override(self):
        run_settings_service.record("rs_a", {"beam_size": 5, "use_gpu": False}, use_gpu=True)
        assert run_settings_service.get("rs_a") == {"beam_size": 5, "use_gpu": True}

    def test_a_run_with_nothing_allowed_clears_the_previous_run(self):
        run_settings_service.record("rs_b", beam_size=5)
        run_settings_service.record("rs_b", audio_path="/x")
        assert run_settings_service.get("rs_b") == {}

    def test_unknown_job_has_none(self):
        assert run_settings_service.get("never_ran") == {}
        assert run_settings_service.get(None) == {}


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

    def test_project_result_json_merges_the_recorded_settings(self):
        run_settings_service.record("rs_c", engine="claude", glossary=True)
        stored = json.loads(jobs_service.project_result_json({"line_count": 2}, "rs_c"))
        assert stored == {"line_count": 2, "run_settings": {"engine": "claude", "glossary": True}}

    def test_a_cue_list_result_still_carries_the_settings(self):
        run_settings_service.record("rs_d", whisper_size="small")
        assert json.loads(jobs_service.project_result_json([{"text": "hi"}], "rs_d")) == {
            "run_settings": {"whisper_size": "small"}}
        assert jobs_service.project_result_json([{"text": "hi"}], "rs_unrecorded") is None

    def test_job_details_expose_only_the_allow_listed_settings(self, isolated_db):
        import time
        run_settings_service.record(
            "rs_job", {"audio_path": "/home/me/a.wav", "beam_size": 5}, asr_backend="whisper",
            model="/home/me/m.bin")
        background_jobs.start_job("rs_job", lambda: background_jobs.set_result("rs_job", {"line_count": 1}))
        deadline = time.time() + 3
        while (background_jobs.get_status("rs_job") or {}).get("status") != "done":
            assert time.time() < deadline
            time.sleep(0.01)
        job = jobs_service.get_job("rs_job")
        assert job["result"] == {"line_count": 1,
                                 "run_settings": {"asr_backend": "whisper", "beam_size": 5}}
        assert "/home/me" not in json.dumps(job)
