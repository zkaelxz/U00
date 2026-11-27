"""
Tests for services/narration_service.py + /api/narration (Migration Slice
33). The LLM is a fake engine; the job body is called directly. No network.
"""
import os

import pytest

import background_jobs
from jobs import job_launch, job_table
from voice import dub_narration
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

    def test_added_chapter_keeps_a_marked_lines_mark(self, isolated_db, monkeypatch):
        did = _drama(isolated_db)
        self._run(isolated_db, did, monkeypatch, lambda ch: {})
        first = isolated_db.load_line_objects(did)
        first[0].en, first[0].mark = "He said: hello.", "mixed"
        isolated_db.save_lines(did, first)
        from engine_backends import llm_tasks, engine_registry
        monkeypatch.setattr(engine_registry, "get_engine", lambda *a, **k: _Engine())
        monkeypatch.setattr(llm_tasks, "tag_speakers_by_id", lambda chunks, engine, known, **k: {})
        narration_service._run_narration_job(f"narration_{did}", did, NOVEL + "\n\n新的一章。", "claude", "k", None)
        after = isolated_db.load_line_objects(did)
        assert (after[0].id, after[0].en, after[0].mark) == (first[0].id, "He said: hello.", "mixed")
        assert after[-1].mark is None

    def test_a_line_edited_while_the_replace_run_saves_is_a_conflict_and_the_edit_is_kept(
            self, isolated_db, monkeypatch):
        did = _drama(isolated_db)
        self._run(isolated_db, did, monkeypatch, lambda ch: {i: "Ann" for i in ch})
        before = self._snapshot(isolated_db, did)
        real = isolated_db.save_lines

        def racy(drama_id, lines, **kw):
            # A person saves an edit in Review between the job's read and its save.
            edited = isolated_db.load_line_objects(drama_id)
            edited[0].en = "typed just now"
            real(drama_id, edited, fields=("en",))
            return real(drama_id, lines, **kw)
        monkeypatch.setattr(isolated_db, "save_lines", racy)
        with pytest.raises(ConflictError):
            self._run(isolated_db, did, monkeypatch, lambda ch: {i: "Bob" for i in ch})
        monkeypatch.undo()
        after = self._snapshot(isolated_db, did)
        assert after[0][3] == "typed just now"
        assert [r[:3] + r[4:] for r in after] == [r[:3] + r[4:] for r in before]

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

    def test_a_stop_between_saving_the_lines_and_clearing_the_marker_does_not_add_them_twice(
            self, isolated_db, monkeypatch):
        import novel_text
        did = _drama(isolated_db)
        self._run(isolated_db, did, monkeypatch, lambda ch: {i: "Ann" for i in ch})
        before = self._snapshot(isolated_db, did)
        text = self._add_text(isolated_db, did, "新的一章。")
        self._tagger(monkeypatch)

        def stop(_dir):
            raise OSError("disk went away")
        monkeypatch.setattr(novel_text, "clear_pending_lines", stop)
        with pytest.raises(OSError):
            narration_service._run_narration_job(
                f"narration_{did}", did, narration_service._pending_tail(did, text), "claude", "k", None, append=True)
        monkeypatch.undo()
        saved = self._snapshot(isolated_db, did)
        assert [r[2] for r in saved[len(before):]] == ["新的一章。"]   # the lines were committed
        assert novel_text.read_pending_lines(isolated_db.drama_dir(did)) is not None   # the marker was not cleared
        with pytest.raises(InvalidInputError):   # the retry sees they are there
            narration_service._pending_tail(did, text)
        assert novel_text.read_pending_lines(isolated_db.drama_dir(did)) is None
        assert self._snapshot(isolated_db, did) == saved

    def test_edited_lines_after_a_failed_save_do_not_look_like_an_applied_append(self, isolated_db, monkeypatch):
        import novel_text
        did = _drama(isolated_db)
        self._run(isolated_db, did, monkeypatch, lambda ch: {i: "Ann" for i in ch})
        text = self._add_text(isolated_db, did, "新的一章。")
        novel_text.write_pending_lines(isolated_db.drama_dir(did), len("新的一章。") + 2, len(text), 1, 1)
        assert narration_service._pending_tail(did, text).strip() == "新的一章。"

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

    def test_labels_are_cleaned_and_capped_like_a_typed_name(self, isolated_db, monkeypatch):
        """A label becomes part of a voice file's name and is shown everywhere; the model's text is
        not checked on its way in."""
        did = _drama(isolated_db)
        long = "Ann " + "x" * 300
        self._run(isolated_db, did, monkeypatch, lambda ch: {0: " Ann\u200b\nLee\x00 ", 1: long})
        assert [ln.speaker for ln in isolated_db.load_line_objects(did)][:2] == ["Ann Lee", long[:100]]
        assert {c["speaker_label"] for c in isolated_db.list_characters(did)} >= {"Ann Lee", long[:100]}
        assert [narration_service._tagged_label(v) for v in ("\u202e\t", 42, None, "")] == ["Narrator"] * 4

    def test_a_tagged_label_passes_the_rules_a_typed_name_does(self):
        """The voice bank refuses a speaker whose name has a slash or "..", so one the model
        wrote that way could never get a saved voice."""
        from services import characters_service
        for raw, want in (("Mei/Lin", "Mei Lin"), ("..\\..\\x", "\u2026 \u2026 x"), ("Ann...", "Ann\u2026")):
            label = narration_service._tagged_label(raw)
            assert label == want and characters_service._clean_label("name", label) == label

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




BOOK = ("第1章 开始\n\n他说：你好。\n\n夜很深了。\n\n"
        "第2章 继续\n\n风很大。\n\n雨停了。\n\n"
        "第3章 结束\n\n天亮了。\n\n他走了。\n")


class TestBusyTitle:
    """Chunk and tag and Add chapters wait for a translation or narration running on the title
    instead of cancelling it (hours of narration, or a paid translation, were thrown away)."""

    @pytest.fixture
    def running(self):
        added = []

        def run(job_id, status="running"):
            job_table._jobs[job_id] = {"status": status, "progress": 0.0, "message": "", "error": None,
                                       "cancel_requested": False, "result": None}
            added.append(job_id)
        yield run
        for job_id in added:
            job_table._jobs.pop(job_id, None)

    @pytest.mark.parametrize("prefix,what", [("translate_", "translation"), ("bulk_translate_", "translation"), ("dub_", "narration")])
    @pytest.mark.parametrize("status", ["running", "queued"])
    def test_start_refuses_and_leaves_the_running_job_alone(self, isolated_db, key, monkeypatch, running,
                                                            prefix, what, status):
        did = _drama(isolated_db)
        running(f"{prefix}{did}", status)
        started = []
        monkeypatch.setattr(job_launch, "start_job", lambda *a, **k: started.append(a) or True)
        with pytest.raises(ConflictError) as caught:
            narration_service.start_narration_run(did)
        assert str(caught.value) == f"Wait for the running {what} to finish, or stop it first."
        assert started == [] and not job_table._jobs[f"{prefix}{did}"]["cancel_requested"]

    def test_another_titles_translation_does_not_count(self, isolated_db, key, monkeypatch, running):
        did, other = _drama(isolated_db), _drama(isolated_db)
        running(f"translate_{other}")
        monkeypatch.setattr(job_launch, "start_job", lambda *a, **k: True)
        assert narration_service.start_narration_run(did) == {"job_id": f"narration_{did}"}

    def test_a_translation_started_while_tagging_stops_the_save_not_the_translation(
            self, isolated_db, monkeypatch, running):
        from engine_backends import llm_tasks, engine_registry
        did = _drama(isolated_db, novel=BOOK)
        monkeypatch.setattr(engine_registry, "get_engine", lambda *a, **k: _Engine())

        def tag(chunks, engine, known, **k):
            running(f"translate_{did}")
            return {i: "Ann" for i in chunks}
        monkeypatch.setattr(llm_tasks, "tag_speakers_by_id", tag)
        with pytest.raises(ConflictError, match="Wait for the running translation"):
            narration_service._run_narration_job(f"narration_{did}", did, BOOK, "claude", "k", None,
                                                 chapters=(2, 2))
        assert isolated_db.load_line_objects(did) == []
        assert not job_table._jobs[f"translate_{did}"]["cancel_requested"]

    def test_added_chapters_wait_too(self, isolated_db, running):
        from core import Line
        did = _drama(isolated_db)
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="旧", en="old")])
        running(f"dub_{did}")
        with pytest.raises(ConflictError, match="Wait for the running narration"):
            narration_service._append_lines(f"narration_{did}", did, [Line(idx=0, start=0, end=1, zh="新")])
        assert [ln.zh for ln in isolated_db.load_line_objects(did)] == ["旧"]
        assert not job_table._jobs[f"dub_{did}"]["cancel_requested"]


class TestChapterSelection:
    def _tagger(self, monkeypatch):
        from engine_backends import llm_tasks, engine_registry
        seen = []
        monkeypatch.setattr(engine_registry, "get_engine", lambda *a, **k: _Engine())

        def tag(chunks, engine, known, **k):
            seen.append(list(chunks.values()))
            return {i: "Ann" for i in chunks}
        monkeypatch.setattr(llm_tasks, "tag_speakers_by_id", tag)
        return seen

    def _run(self, did, chapters, fresh=False):
        narration_service._run_narration_job(
            f"narration_{did}", did, BOOK, "claude", "k", None, fresh=fresh, chapters=chapters)

    def test_chapter_spans_number_like_the_chapter_count(self):
        import novel_text
        spans = novel_text.chapter_spans(BOOK)
        assert len(spans) == novel_text.chapter_count(BOOK) == 3
        assert BOOK[spans[1][0]:spans[1][1]].startswith("第2章")

    def test_one_chapter_tags_and_pays_for_only_that_chapter(self, isolated_db, monkeypatch):
        did = _drama(isolated_db, novel=BOOK)
        seen = self._tagger(monkeypatch)
        self._run(did, (2, 2))
        assert seen == [["第2章 继续", "风很大。", "雨停了。"]]
        lines = isolated_db.load_line_objects(did)
        assert [ln.zh for ln in lines] == ["第2章 继续", "风很大。", "雨停了。"]
        assert [ln.idx for ln in lines] == [0, 1, 2]

    def test_a_later_run_adds_its_chapters_after_and_before_without_touching_the_rest(
            self, isolated_db, monkeypatch):
        did = _drama(isolated_db, novel=BOOK)
        self._tagger(monkeypatch)
        self._run(did, (2, 2))
        mid = isolated_db.load_line_objects(did)
        mid[1].en, mid[1].reviewed = "The wind is strong.", True
        isolated_db.save_lines(did, mid)
        self._run(did, (3, 3))
        self._run(did, (1, 1))
        lines = isolated_db.load_line_objects(did)
        assert [ln.zh for ln in lines] == [
            "第1章 开始", "他说：你好。", "夜很深了。", "第2章 继续", "风很大。", "雨停了。",
            "第3章 结束", "天亮了。", "他走了。"]
        assert [ln.idx for ln in lines] == list(range(9))
        kept = {ln.zh: ln for ln in lines}["风很大。"]
        assert (kept.id, kept.en, kept.reviewed) == (mid[1].id, "The wind is strong.", True)

    def test_rerunning_a_tagged_chapter_keeps_its_translation_and_leaves_others_alone(
            self, isolated_db, monkeypatch):
        did = _drama(isolated_db, novel=BOOK)
        seen = self._tagger(monkeypatch)
        self._run(did, (1, 3))
        lines = isolated_db.load_line_objects(did)
        for ln in lines:
            ln.en = "E:" + ln.zh
            ln.speaker = "Old"
        isolated_db.save_lines(did, lines)
        before = {ln.id: ln for ln in isolated_db.load_line_objects(did)}
        seen.clear()
        self._run(did, (2, 2), fresh=True)
        after = isolated_db.load_line_objects(did)
        assert [ln.id for ln in after] == [ln.id for ln in lines]
        for ln in after:
            assert ln.en == "E:" + ln.zh
            assert ln.speaker == ("Ann" if ln.zh in ("第2章 继续", "风很大。", "雨停了。") else before[ln.id].speaker)
        assert len(seen) == 1 and len(seen[0]) == 3

    def test_lines_edited_in_review_refuse_a_chapter_run_before_anything_is_paid(
            self, isolated_db, key):
        did = _drama(isolated_db, novel=BOOK)
        from core import Line
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="第1章 开始 (edited)")])
        with pytest.raises(ConflictError):
            narration_service.start_narration_run(did, chapter_from=2, chapter_to=2)

    def test_bad_range_is_refused(self, isolated_db, key):
        did = _drama(isolated_db, novel=BOOK)
        for first, last in ((4, 4), (3, 2)):
            with pytest.raises(InvalidInputError):
                narration_service.start_narration_run(did, chapter_from=first, chapter_to=last)

    def test_whole_range_is_a_normal_run(self, isolated_db, key, monkeypatch):
        did = _drama(isolated_db, novel=BOOK)
        calls = []
        monkeypatch.setattr(job_launch, "start_job", lambda *a, **k: calls.append(k) or True)
        narration_service.start_narration_run(did, chapter_from=1, chapter_to=3)
        assert calls[0]["chapters"] is None

    def test_estimate_covers_only_the_chosen_chapters(self, isolated_db, key):
        did = _drama(isolated_db, novel=BOOK)
        whole = narration_service.estimate_tag_cost(did, "claude")
        one = narration_service.estimate_tag_cost(did, "claude", chapter_from=2, chapter_to=2)
        assert (whole["chunk_count"], one["chunk_count"], one["chapters"]) == (9, 3, 1)
        assert 0 < one["estimated_usd"] < whole["estimated_usd"]
        assert whole["cap_applies"] and not whole["monthly_cap_set"]

    def test_estimate_is_free_without_a_key_and_ollama_is_capped(self, isolated_db, monkeypatch):
        monkeypatch.setattr(settings_service, "resolve_key", lambda *a, **k: None)
        did = _drama(isolated_db, novel=BOOK)
        assert narration_service.estimate_tag_cost(did, "claude")["estimated_usd"] == 0.0
        hosted = narration_service.estimate_tag_cost(did, "ollama", "gemma4:31b-cloud")
        assert hosted["cap_applies"]


class TestOneNarrator:
    def test_no_engine_is_built_or_called_and_every_line_is_narrator(self, isolated_db, key, monkeypatch):
        from engine_backends import llm_tasks, engine_registry

        def boom(*a, **k):
            raise AssertionError("an AI engine was used")
        monkeypatch.setattr(engine_registry, "get_engine", boom)
        monkeypatch.setattr(llm_tasks, "tag_speakers_by_id", boom)
        did = _drama(isolated_db)
        launched = []
        monkeypatch.setattr(job_launch, "start_job",
                            lambda job_id, target, *a, **k: launched.append((target, a, k)) or True)
        narration_service.start_narration_run(did, "claude", one_narrator=True)
        target, args, kw = launched[0]
        assert args[4] is None and kw["one_narrator"]
        results = []
        monkeypatch.setattr(background_jobs, "set_result", lambda job_id, res: results.append(res))
        target(*args, **{k: v for k, v in kw.items() if k in ("fresh", "append", "chapters", "one_narrator")})
        lines = isolated_db.load_line_objects(did)
        assert lines and {ln.speaker for ln in lines} == {"Narrator"}
        assert "notice" not in results[0]

    def test_works_for_chosen_chapters_too(self, isolated_db, key, monkeypatch):
        did = _drama(isolated_db, novel=BOOK)
        narration_service._run_narration_job(
            f"narration_{did}", did, BOOK, "claude", None, None, chapters=(2, 3), one_narrator=True)
        assert [ln.speaker for ln in isolated_db.load_line_objects(did)] == ["Narrator"] * 6


class TestAppendEstimate:
    def test_prices_only_the_text_an_add_left_without_lines(self, isolated_db, key):
        import novel_text
        from core import Line
        did = _drama(isolated_db, novel=BOOK)
        isolated_db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=z) for i, z in enumerate(
            ["第1章 开始", "他说：你好。", "夜很深了。", "第2章 继续", "风很大。", "雨停了。"])])
        tail = "第3章 结束\n\n天亮了。\n\n他走了。\n"
        novel_text.write_pending_lines(isolated_db.drama_dir(did), len(tail), len(BOOK), 6, 3)
        whole = narration_service.estimate_tag_cost(did, "claude")
        added = narration_service.estimate_tag_cost(did, "claude", append=True)
        assert (whole["chunk_count"], added["chunk_count"]) == (9, 3)

    def test_nothing_waiting_is_refused(self, isolated_db, key):
        did = _drama(isolated_db)
        with pytest.raises(InvalidInputError):
            narration_service.estimate_tag_cost(did, "claude", append=True)
