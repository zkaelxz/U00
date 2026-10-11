"""Auto Whisper initial_prompt (parity audit B1): transcribe_service.
build_auto_initial_prompt is shared by cli.cmd_align, the config response
and a transcribe run whose prompt is empty. Fully mocked."""
import argparse
import contextlib
import io
import os

import pytest

import background_jobs
from services import autotune_service, transcribe_service
from services.service_errors import NotFoundError


def _novel(db, did, text):
    with open(os.path.join(db.drama_dir(did), "raw_novel_context.txt"), "w", encoding="utf-8") as f:
        f.write(text)


def _drama_with_glossary(db):
    sid = db.get_or_create_series("S")
    db.upsert_glossary_term(sid, "苏杉", "Su Shan")
    return db.create_drama(title_en="D", series_id=sid)


class TestBuildAutoInitialPrompt:
    def test_unknown_drama_raises(self, isolated_db):
        with pytest.raises(NotFoundError):
            transcribe_service.build_auto_initial_prompt(999999)

    def test_neither(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        assert transcribe_service.build_auto_initial_prompt(did) == ""

    def test_glossary_only(self, isolated_db):
        did = _drama_with_glossary(isolated_db)
        assert transcribe_service.build_auto_initial_prompt(did) == "苏杉。"

    def test_novel_only(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        _novel(isolated_db, did, "第一章\n  他走了。")
        assert transcribe_service.build_auto_initial_prompt(did) == "第一章 他走了。"

    def test_both_names_first(self, isolated_db):
        did = _drama_with_glossary(isolated_db)
        _novel(isolated_db, did, "他走了。")
        assert transcribe_service.build_auto_initial_prompt(did) == "苏杉。他走了。"

    def test_truncated_to_the_cli_limit(self, isolated_db):
        did = _drama_with_glossary(isolated_db)
        _novel(isolated_db, did, "字" * 5000)
        prompt = transcribe_service.build_auto_initial_prompt(did)
        # names first, then the novel excerpt capped at 800 characters
        assert prompt.startswith("苏杉。")
        assert len(prompt) == 3 + 800
        sid = isolated_db.get_drama(did)["series_id"]
        for i in range(40):
            isolated_db.upsert_glossary_term(sid, f"名字{i:02d}", f"N{i}")
        # combine_initial_prompt's overall 900-character cap, same as the CLI
        assert len(transcribe_service.build_auto_initial_prompt(did)) == 900

    def test_extra_names_join_like_streamlit(self, isolated_db):
        did = _drama_with_glossary(isolated_db)
        assert transcribe_service.build_auto_initial_prompt(did, "沈清疑、云隐宗") == "苏杉、沈清疑、云隐宗。"
        _novel(isolated_db, did, "他走了。")
        assert transcribe_service.build_auto_initial_prompt(did, " 沈清疑 ") == "苏杉、沈清疑。他走了。"

    def test_extra_names_only(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        assert transcribe_service.build_auto_initial_prompt(did, "沈清疑") == "沈清疑。"

    def test_config_exposes_it(self, isolated_db):
        did = _drama_with_glossary(isolated_db)
        assert transcribe_service.get_transcribe_config(did)["auto_initial_prompt"] == "苏杉。"


def _audio_drama(db):
    did = _drama_with_glossary(db)
    db.update_drama(did, transcript_mode="whisper", audio_filename="audio.wav")
    with open(os.path.join(db.drama_dir(did), "audio.wav"), "wb") as f:
        f.write(b"x")
    return did


def _capture(monkeypatch):
    from tests.test_transcribe_service import _capture_worker_start
    return _capture_worker_start(monkeypatch)


class TestRunUsesAutoPrompt:
    @pytest.mark.parametrize("prompt", ["", "   "])
    def test_empty_prompt_uses_auto(self, isolated_db, monkeypatch, prompt):
        did = _audio_drama(isolated_db)
        captured = _capture(monkeypatch)
        transcribe_service.start_transcribe_run(did, initial_prompt=prompt)
        assert captured["initial_prompt"] == "苏杉。"

    def test_extra_names_are_added_to_auto(self, isolated_db, monkeypatch):
        did = _audio_drama(isolated_db)
        captured = _capture(monkeypatch)
        transcribe_service.start_transcribe_run(did, extra_names="沈清疑")
        assert captured["initial_prompt"] == "苏杉、沈清疑。"

    def test_autotune_uses_auto_plus_extra_names(self, isolated_db, monkeypatch):
        did = _audio_drama(isolated_db)
        seen = {}

        def fake_process_job(job_id, target, args=(), **k):
            seen["args"] = args
            return True
        monkeypatch.setattr(background_jobs, "start_process_job", fake_process_job)
        autotune_service.start_autotune_run(did, extra_names="沈清疑")
        assert "苏杉、沈清疑。" in seen["args"]
        autotune_service.start_autotune_run(did, initial_prompt="全替换", extra_names="沈清疑")
        assert "全替换" in seen["args"]

    def test_typed_prompt_wins(self, isolated_db, monkeypatch):
        did = _audio_drama(isolated_db)
        captured = _capture(monkeypatch)
        transcribe_service.start_transcribe_run(did, initial_prompt="沈清疑", extra_names="x")
        assert captured["initial_prompt"] == "沈清疑"


class TestApi:
    @pytest.fixture
    def client(self, isolated_db):
        pytest.importorskip("fastapi")
        pytest.importorskip("httpx")
        from fastapi.testclient import TestClient
        from api.api_config import ApiSettings
        from api.server import create_app
        return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)

    def test_config_response_has_auto_prompt(self, client, isolated_db):
        did = _drama_with_glossary(isolated_db)
        r = client.get(f"/api/transcribe/dramas/{did}/config")
        assert r.status_code == 200
        assert r.json()["auto_initial_prompt"] == "苏杉。"

    def test_run_without_prompt_uses_auto(self, client, isolated_db, monkeypatch):
        did = _audio_drama(isolated_db)
        captured = _capture(monkeypatch)
        r = client.post(f"/api/transcribe/dramas/{did}/run", json={})
        assert r.status_code == 200, r.text
        assert captured["initial_prompt"] == "苏杉。"

    def test_run_with_extra_names(self, client, isolated_db, monkeypatch):
        did = _audio_drama(isolated_db)
        captured = _capture(monkeypatch)
        r = client.post(f"/api/transcribe/dramas/{did}/run", json={"extra_names": "沈清疑"})
        assert r.status_code == 200, r.text
        assert captured["initial_prompt"] == "苏杉、沈清疑。"

    def test_extra_names_length_limit(self, client, isolated_db):
        did = _audio_drama(isolated_db)
        r = client.post(f"/api/transcribe/dramas/{did}/run", json={"extra_names": "x" * 1001})
        assert r.status_code in (400, 422)
        r = client.post(f"/api/transcribe/dramas/{did}/autotune", json={"extra_names": "x" * 1001})
        assert r.status_code in (400, 422)


def test_cli_align_uses_the_helper(isolated_db, monkeypatch):
    import cli
    did = isolated_db.create_drama(title_en="D", audio_filename="audio.wav")
    ddir = isolated_db.drama_dir(did)
    with open(os.path.join(ddir, "audio.wav"), "wb") as f:
        f.write(b"x")
    with open(os.path.join(ddir, "transcript.txt"), "w", encoding="utf-8") as f:
        f.write("你好")
    calls, seen = [], {}
    monkeypatch.setattr(transcribe_service, "build_auto_initial_prompt",
                        lambda d: calls.append(d) or "HELPER")

    def fake_transcribe(audio_path, model_size, **kw):
        seen["initial_prompt"] = kw.get("initial_prompt")
        return [{"start": 0.0, "end": 1.0, "text": "你好"}]
    monkeypatch.setattr(cli, "transcribe_for_timing", fake_transcribe)
    with contextlib.redirect_stdout(io.StringIO()):
        cli.cmd_align(argparse.Namespace(id=did, whisper_size=None, fast=False))
    assert calls and set(calls) == {did}
    assert seen["initial_prompt"] == "HELPER"
