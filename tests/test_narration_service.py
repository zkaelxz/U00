"""
Tests for services/narration_service.py + /api/narration (Migration Slice
33). The LLM is a fake engine; the job body is called directly. No network.
"""
import os

import pytest

import background_jobs
from jobs import job_launch, job_table
import dub_narration
from services import narration_service, settings_service
from lib.errors import (ConflictError,
                                     InvalidInputError, NotFoundError)

NOVEL = "他说：你好。\n\n夜很深了。风很大。"


def _drama(db, novel=NOVEL, **fields):
    fields.setdefault("title_en", "N")
    fields.setdefault("content_mode", "novel_narration")
    did = db.create_drama(**fields)
    if novel is not None:
        with open(os.path.join(db.drama_dir(did), dub_narration.NOVEL_SOURCE_FILENAME), "w",
                  encoding="utf-8") as f:
            f.write(novel)
    return did


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setattr(settings_service, "resolve_key",
                        lambda k, *a, **kw: "sk-secret" if k == "claude" else None)


class TestStart:
    def test_unknown_drama(self, isolated_db):
        with pytest.raises(NotFoundError):
            narration_service.start_narration_run(999)

    def test_no_novel_source(self, isolated_db, key):
        did = _drama(isolated_db, novel=None)
        with pytest.raises(InvalidInputError):
            narration_service.start_narration_run(did)

    def test_bad_engine(self, isolated_db, key):
        did = _drama(isolated_db)
        with pytest.raises(InvalidInputError):
            narration_service.start_narration_run(did, "fake_mt")

    def test_no_key_still_starts_the_job(self, isolated_db, monkeypatch):
        monkeypatch.setattr(settings_service, "resolve_key", lambda *a, **k: None)
        did = _drama(isolated_db)
        calls = []
        monkeypatch.setattr(job_launch, "start_job",
                            lambda *a, **k: calls.append(a) or True)
        assert narration_service.start_narration_run(did) == {"job_id": f"narration_{did}"}
        assert calls[0][-2] is None  # api_key, then model

    def test_starts_job(self, isolated_db, key, monkeypatch):
        did = _drama(isolated_db)
        calls = []
        monkeypatch.setattr(job_launch, "start_job",
                            lambda job_id, target, *a, **k: calls.append((job_id, a)) or True)
        assert narration_service.start_narration_run(did) == {"job_id": f"narration_{did}"}
        assert calls[0][1][3:] == ("claude", "sk-secret", None)

    def test_duplicate_conflict(self, isolated_db, key, monkeypatch):
        did = _drama(isolated_db)
        monkeypatch.setattr(job_launch, "start_job", lambda *a, **k: False)
        with pytest.raises(ConflictError):
            narration_service.start_narration_run(did)


class _Engine:
    supports_reference = True
    model = "fake"


class TestJob:
    def _run(self, db, did, monkeypatch, speakers):
        from engine_backends import llm_tasks, engine_registry
        monkeypatch.setattr(engine_registry, "get_engine", lambda *a, **k: _Engine())
        monkeypatch.setattr(llm_tasks, "tag_speakers_by_id",
                            lambda chunks, engine, known, **k: speakers(chunks))
        job_id = f"narration_{did}"
        narration_service._run_narration_job(job_id, did, NOVEL, "claude", "k", None)

    def test_replaces_lines_tags_speakers_and_snapshots(self, isolated_db, monkeypatch):
        from core import Line
        did = _drama(isolated_db)
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="旧", en="old")])
        self._run(isolated_db, did, monkeypatch,
                  lambda ch: {i: ("Ann" if "说" in c else "Narrator") for i, c in ch.items()})
        lines = isolated_db.load_line_objects(did)
        assert [ln.zh for ln in lines] and all(ln.id for ln in lines)
        assert {ln.speaker for ln in lines} == {"Ann", "Narrator"}
        assert all("旧" not in ln.zh for ln in lines)
        assert isolated_db.get_drama(did)["status"] == "aligned"
        labels = {c["speaker_label"] for c in isolated_db.list_characters(did)}
        assert {"Ann", "Narrator"} <= labels

    def test_added_chapter_keeps_existing_translation_and_row_ids(self, isolated_db, monkeypatch):
        did = _drama(isolated_db)
        self._run(isolated_db, did, monkeypatch, lambda ch: {})
        first = isolated_db.load_line_objects(did)
        first[0].en, first[0].reviewed = "He said: hello.", True
        isolated_db.save_lines(did, first)
        ids = [ln.id for ln in first]
        from engine_backends import llm_tasks, engine_registry
        monkeypatch.setattr(engine_registry, "get_engine", lambda *a, **k: _Engine())
        monkeypatch.setattr(llm_tasks, "tag_speakers_by_id", lambda chunks, engine, known, **k: {})
        narration_service._run_narration_job(f"narration_{did}", did, NOVEL + "\n\n新的一章。", "claude", "k", None)
        after = isolated_db.load_line_objects(did)
        assert [ln.id for ln in after[:len(ids)]] == ids
        assert after[0].en == "He said: hello." and after[0].reviewed
        assert after[-1].zh == "新的一章。" and after[-1].en == ""

    def _add_text(self, db, did, added):
        """What the add step leaves behind: the text with `added` appended and the pending marker."""
        import novel_text
        path = os.path.join(db.drama_dir(did), novel_text.NOVEL_SOURCE_FILENAME)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        text = text.rstrip() + "\n\n" + added
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        novel_text.write_pending_lines(db.drama_dir(did), len(added) + 2, len(text))
        return text

    def _tagger(self, monkeypatch, label="Bob", fail=False):
        from engine_backends import llm_tasks, engine_registry
        seen = []

        def tag(chunks, engine, known, **k):
            if fail:
                raise RuntimeError("engine down")
            seen.append(dict(chunks))
            return {i: label for i in chunks}
        monkeypatch.setattr(engine_registry, "get_engine", lambda *a, **k: _Engine())
        monkeypatch.setattr(llm_tasks, "tag_speakers_by_id", tag)
        return seen

    def _snapshot(self, db, did):
        return [(ln.id, ln.idx, ln.zh, ln.en, ln.reviewed, ln.speaker, ln.flag_note) for ln in db.load_line_objects(did)]

    def test_append_run_leaves_saved_lines_alone_and_tags_only_the_new_text(self, isolated_db, monkeypatch):
        did = _drama(isolated_db)
        self._run(isolated_db, did, monkeypatch, lambda ch: {i: "Ann" for i in ch})
        lines = isolated_db.load_line_objects(did)
        lines[0].zh, lines[0].en, lines[0].reviewed = "他说：你好！（我改过）", "He said hi.", True   # edited in Review
        lines[1].flag_note = "check"
        isolated_db.save_lines(did, lines)
        before = self._snapshot(isolated_db, did)
        added = "新的一章。"
        text = self._add_text(isolated_db, did, added)
        seen = self._tagger(monkeypatch)
        narration_service._run_narration_job(
            f"narration_{did}", did, narration_service._pending_tail(did, text), "claude", "k", None, append=True)
        after = self._snapshot(isolated_db, did)
        assert after[:len(before)] == before
        assert [(a[2], a[3], a[5]) for a in after[len(before):]] == [(added, "", "Bob")]
        assert after[-1][1] == len(before) and [list(c.values()) for c in seen] == [[added]]
        assert any("before adding chapters" in h["label"] for h in isolated_db.list_line_history(did))
        import novel_text
        assert novel_text.read_pending_lines(isolated_db.drama_dir(did)) is None

    def test_append_start_takes_the_amount_from_the_server_and_refuses_when_nothing_waits(self, isolated_db, key, monkeypatch):
        did = _drama(isolated_db)
        with pytest.raises(InvalidInputError):                      # nothing waiting
            narration_service.start_narration_run(did, append=True)
        self._run(isolated_db, did, monkeypatch, lambda ch: {})
        with pytest.raises(InvalidInputError):                      # still nothing waiting
            narration_service.start_narration_run(did, append=True)
        added = "新的一章。"
        self._add_text(isolated_db, did, added)
        calls = []
        monkeypatch.setattr(job_launch, "start_job", lambda job_id, target, *a, **k: calls.append((a, k)) or True)
        narration_service.start_narration_run(did, append=True)
        assert calls[0][0][2] == "\n\n" + added and calls[0][1]["append"] is True

    def test_append_start_refuses_when_the_text_changed_since_the_add(self, isolated_db, key, monkeypatch):
        import novel_text
        did = _drama(isolated_db)
        self._run(isolated_db, did, monkeypatch, lambda ch: {})
        self._add_text(isolated_db, did, "新的一章。")
        with open(os.path.join(isolated_db.drama_dir(did), novel_text.NOVEL_SOURCE_FILENAME), "a", encoding="utf-8") as f:
            f.write("又改了。")
        with pytest.raises(ConflictError):
            narration_service.start_narration_run(did, append=True)

    def test_a_line_changed_while_the_append_job_runs_is_a_conflict_and_nothing_is_lost(self, isolated_db, monkeypatch):
        did = _drama(isolated_db)
        self._run(isolated_db, did, monkeypatch, lambda ch: {i: "Ann" for i in ch})
        text = self._add_text(isolated_db, did, "新的一章。")
        self._tagger(monkeypatch)
        tail = narration_service._pending_tail(did, text)
        real = isolated_db.save_lines

        def racy(drama_id, lines, **kw):
            # A person edits a line in Review between the job's read and its save.
            edited = isolated_db.load_line_objects(drama_id)
            edited[0].en = "typed just now"
            real(drama_id, edited, fields=("en",))
            return real(drama_id, lines, **kw)
        monkeypatch.setattr(isolated_db, "save_lines", racy)
        with pytest.raises(ConflictError):
            narration_service._run_narration_job(f"narration_{did}", did, tail, "claude", "k", None, append=True)
        monkeypatch.undo()
        rows = isolated_db.load_line_objects(did)
        assert rows[0].en == "typed just now" and all(r.zh != "新的一章。" for r in rows)
        import novel_text
        assert novel_text.read_pending_lines(isolated_db.drama_dir(did)) is not None   # still waiting: a retry can finish it

    def test_a_failed_append_keeps_the_marker_and_a_retry_adds_the_lines_once(self, isolated_db, monkeypatch):
        import novel_text
        did = _drama(isolated_db)
        self._run(isolated_db, did, monkeypatch, lambda ch: {i: "Ann" for i in ch})
        before = self._snapshot(isolated_db, did)
        text = self._add_text(isolated_db, did, "新的一章。")
        self._tagger(monkeypatch, fail=True)
        with pytest.raises(RuntimeError):
            narration_service._run_narration_job(
                f"narration_{did}", did, narration_service._pending_tail(did, text), "claude", "k", None, append=True)
        assert self._snapshot(isolated_db, did) == before
        assert novel_text.read_pending_lines(isolated_db.drama_dir(did)) is not None
        self._tagger(monkeypatch)
        narration_service._run_narration_job(
            f"narration_{did}", did, narration_service._pending_tail(did, text), "claude", "k", None, append=True)
        assert [r[2] for r in self._snapshot(isolated_db, did)[len(before):]] == ["新的一章。"]

    def test_a_repeated_append_is_refused_and_adds_no_second_copy(self, isolated_db, key, monkeypatch):
        did = _drama(isolated_db)
        self._run(isolated_db, did, monkeypatch, lambda ch: {i: "Ann" for i in ch})
        text = self._add_text(isolated_db, did, "新的一章。")
        self._tagger(monkeypatch)
        narration_service._run_narration_job(
            f"narration_{did}", did, narration_service._pending_tail(did, text), "claude", "k", None, append=True)
        count = len(isolated_db.load_lines(did))
        with pytest.raises(InvalidInputError):
            narration_service.start_narration_run(did, append=True)
        assert len(isolated_db.load_lines(did)) == count

    def test_no_key_cuts_locally_and_marks_every_line_narrator(self, isolated_db, monkeypatch):
        from engine_backends import llm_tasks, engine_registry

        def never(*a, **k):
            raise AssertionError("no AI call without a key")
        monkeypatch.setattr(engine_registry, "get_engine", never)
        monkeypatch.setattr(llm_tasks, "tag_speakers_by_id", never)
        did = _drama(isolated_db)
        job_id = f"narration_{did}"
        results = []
        monkeypatch.setattr(background_jobs, "set_result", lambda jid, r: results.append(r))
        narration_service._run_narration_job(job_id, did, NOVEL, "claude", None, None)
        lines = isolated_db.load_line_objects(did)
        assert lines and {ln.speaker for ln in lines} == {"Narrator"}
        assert isolated_db.get_drama(did)["status"] == "aligned"
        result = results[-1]
        assert result["notice"] == ("No AI key set, so every line is marked Narrator. "
                                    "Add a key in Settings to tag speakers.")
        assert result["line_count"] == len(lines)

    def test_labels_attach_by_idx_missing_and_extra_ids_are_safe(self, isolated_db, monkeypatch):
        did = _drama(isolated_db)
        self._run(isolated_db, did, monkeypatch, lambda ch: {1: "Ann", 999: "Ghost"})
        lines = isolated_db.load_line_objects(did)
        assert len(lines) > 1
        assert [ln.speaker for ln in lines] == ["Ann" if i == 1 else "Narrator" for i in range(len(lines))]
        assert "Ghost" not in {c["speaker_label"] for c in isolated_db.list_characters(did)}

    def test_failure_redacts_secret_in_job_error(self, isolated_db, monkeypatch, key):
        did = _drama(isolated_db)
        from engine_backends import llm_tasks, engine_registry
        monkeypatch.setattr(engine_registry, "get_engine", lambda *a, **k: _Engine())

        def boom(*a, **k):
            raise RuntimeError("bad key sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789")
        monkeypatch.setattr(llm_tasks, "tag_speakers_by_id", boom)
        job_id = f"narration_{did}"
        assert job_launch.start_job(
            job_id, narration_service._run_narration_job, job_id, did, NOVEL, "claude", "k", None)
        import time
        for _ in range(100):
            st = background_jobs.get_status(job_id)
            if st["status"] != "running":
                break
            time.sleep(0.05)
        assert st["status"] == "error"
        assert "abcdefghijklmnop" not in (st["error"] or "")
        assert isolated_db.load_line_objects(did) == []


class TestApi:
    def test_config(self, client, isolated_db, key):
        did = _drama(isolated_db)
        body = client.get(f"/api/narration/dramas/{did}/config").json()
        assert body["has_novel_source"] and body["is_narration"]
        assert {e["key"]: e["key_configured"] for e in body["engines"]}["claude"] is True
        assert "sk-secret" not in str(body) and "novel_narration_source" not in str(body)
        assert client.get("/api/narration/dramas/999/config").status_code == 404

    def test_run_status_codes(self, client, isolated_db, key, monkeypatch):
        assert client.post("/api/narration/dramas/999/run", json={}).status_code == 404
        no_src = _drama(isolated_db, novel=None)
        assert client.post(f"/api/narration/dramas/{no_src}/run", json={}).status_code == 422
        did = _drama(isolated_db)
        monkeypatch.setattr(job_launch, "start_job", lambda *a, **k: True)
        r = client.post(f"/api/narration/dramas/{did}/run", json={})
        assert r.status_code == 200 and r.json() == {"job_id": f"narration_{did}"}
        monkeypatch.setattr(job_launch, "start_job", lambda *a, **k: False)
        assert client.post(f"/api/narration/dramas/{did}/run", json={}).status_code == 409


class TestNarrationCancel:
    def test_tag_speakers_stops_before_the_next_batch(self, monkeypatch):
        from engine_backends import shared, llm_tasks
        sent = []
        monkeypatch.setattr("engine_backends.llm_tasks.call_llm_json",
                            lambda engine, prompt, **k: sent.append(prompt) or "{}")
        state = {"cancelled": False}

        def check():
            if state["cancelled"]:
                raise background_jobs.JobCancelled("j")
        orig_batch = shared._id_keyed_batch_request

        def batch(*a, **k):
            out = orig_batch(*a, **k)
            state["cancelled"] = True
            return out
        monkeypatch.setattr("engine_backends.llm_tasks._id_keyed_batch_request", batch)
        with pytest.raises(background_jobs.JobCancelled):
            llm_tasks.tag_speakers_by_id(
                {i: "文" for i in range(40)}, _Engine(), batch_size=15, cancel_check=check)
        # Only the first batch (plus its one retry for missing ids) was paid for.
        assert len(sent) == 2

    def test_cancelled_job_saves_nothing_and_does_not_finish(self, isolated_db, monkeypatch):
        from engine_backends import llm_tasks, engine_registry
        from core import Line
        did = _drama(isolated_db)
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="旧", en="old")])
        job_id = f"narration_{did}"
        monkeypatch.setattr(engine_registry, "get_engine", lambda *a, **k: _Engine())

        def tag(chunks, engine, known, **k):
            job_table._jobs[job_id]["cancel_requested"] = True
            return {i: "Ann" for i in chunks}   # an engine that finished anyway
        monkeypatch.setattr(llm_tasks, "tag_speakers_by_id", tag)
        job_table._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                         "error": None, "cancel_requested": False, "result": None}
        try:
            with pytest.raises(background_jobs.JobCancelled):
                narration_service._run_narration_job(job_id, did, NOVEL, "claude", "k", None)
        finally:
            job_table._jobs.pop(job_id, None)
        assert [ln.zh for ln in isolated_db.load_line_objects(did)] == ["旧"]
        assert isolated_db.get_drama(did)["status"] != "aligned"


class TestGpuSlot:
    def _start(self, isolated_db, key, monkeypatch, engine, model=None):
        did = _drama(isolated_db)
        seen = {}
        monkeypatch.setattr(settings_service, "resolve_key", lambda *a, **k: "k")
        monkeypatch.setattr(job_launch, "start_job",
                            lambda job_id, target, *a, **k: seen.update(k) or True)
        narration_service.start_narration_run(did, engine, model)
        return seen

    def test_an_ollama_run_takes_the_gpu_slot(self, isolated_db, key, monkeypatch):
        assert self._start(isolated_db, key, monkeypatch, "ollama")["gpu_touching"] is True

    def test_a_cloud_engine_does_not(self, isolated_db, key, monkeypatch):
        assert self._start(isolated_db, key, monkeypatch, "claude")["gpu_touching"] is False
