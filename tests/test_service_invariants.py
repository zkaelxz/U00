"""Service-layer restatements of data-safety invariants that, before the
Streamlit retirement, were only asserted by AppTest (UI-class) tests in
tests/test_workspace_tab.py. Each class names the Streamlit test class it
came from, so the invariant survives when the tab and its tests are deleted
(docs/streamlit-test-triage.md, section 1 notes). Fully mocked: isolated_db,
no model, GPU or network."""
import os
import threading
import time

import pytest

import background_jobs
import core as core_module
import db
import resegment
from core import Line
from services import drama_service, restructure_service, transcribe_service
from services.service_errors import ConflictError, InvalidInputError

LONG = "他说他明天会来，可是我不太相信他。因为他上次也是这么说的，结果根本没有出现"
FIRST = "他说他明天会来，可是我不太相信他。"
SECOND = "因为他上次也是这么说的，结果根本没有出现"


@pytest.fixture(autouse=True)
def _env(isolated_db, monkeypatch):
    background_jobs.clear_all_jobs()
    monkeypatch.setattr(resegment, "word_boundaries", lambda *a, **k: None)
    monkeypatch.setattr(core_module, "load_whisper_model", lambda *a, **k: object())
    yield
    background_jobs.clear_all_jobs()


def _wait(job_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = background_jobs.get_status(job_id)
        if job and job["status"] not in ("running", "queued"):
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish")


def _snapshot(did):
    return [(ln.id, ln.zh, ln.en) for ln in db.load_line_objects(did)]


def _append_line(did, zh="新的一行。"):
    lines = db.load_line_objects(did)
    lines.append(Line(idx=len(lines), start=20.0, end=21.0, zh=zh))
    db.save_lines(did, lines)


# ---------------------------------------------------------------------------
# From TestTranscriptionCompletionDoesNotWipeExistingLines
# ---------------------------------------------------------------------------

def _transcript_drama():
    did = db.create_drama(title_en="T", media_type="audio_drama", content_mode="audio_drama",
                          status="aligned", audio_filename="audio.wav",
                          transcript_mode="have_transcript", source_language="zh")
    ddir = db.drama_dir(did)
    os.makedirs(ddir, exist_ok=True)
    with open(os.path.join(ddir, "audio.wav"), "wb") as f:
        f.write(b"x")
    db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="原文", en="Original")])
    return did, ddir


def _run_job_body(did, ddir, text):
    job_id = f"transcribe_{did}"
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                     "error": None, "cancel_requested": False, "result": None}
    transcribe_service._run_transcribe_and_apply_job(
        job_id, did, os.path.join(ddir, "audio.wav"), "have_transcript", text, "zh",
        "simplified", "medium", 5, 300, 0.5, False, "auto", False, False, False, None, None, None)
    return background_jobs.get_status(job_id)["result"]


class TestTranscriptionCompletionInvariants:
    def test_job_applies_itself_with_the_text_captured_at_start(self, monkeypatch):
        """No client has to be present when the job finishes (the "new
        session" case), and the text used is the one passed at start, not
        re-read later: a later change to the request/UI can't reach it."""
        did, _ = _transcript_drama()
        release = threading.Event()
        monkeypatch.setattr(transcribe_service, "transcribe_for_timing",
                            lambda *a, **k: (release.wait(5.0),
                                             [{"start": 0.0, "end": 1.0, "text": "你好"}])[1])
        text = ["真实台词"]
        out = transcribe_service.start_transcribe_run(did, transcript_text=text[0])
        text[0] = "被修改的文本"  # the caller's copy changes while the job runs
        release.set()
        job = _wait(out["job_id"])
        assert job["status"] == "done", job
        assert [ln.zh for ln in db.load_line_objects(did)] == ["真实台词"]

    def test_zero_aligned_lines_against_existing_lines_is_refused(self, monkeypatch):
        did, ddir = _transcript_drama()
        before = _snapshot(did)
        monkeypatch.setattr(transcribe_service, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "你好"}])
        monkeypatch.setattr(transcribe_service, "align_transcript_to_timing", lambda *a, **k: [])
        result = _run_job_body(did, ddir, "真实台词")
        assert result["failed_reason"] == "empty_kept_existing"
        assert _snapshot(did) == before
        assert db.list_line_history(did) == []

    def test_history_snapshot_is_taken_before_a_legitimate_replacement(self, monkeypatch):
        did, ddir = _transcript_drama()
        monkeypatch.setattr(transcribe_service, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "你好"}])
        _run_job_body(did, ddir, "真实台词")
        history = db.list_line_history(did)
        assert len(history) == 1 and history[0]["label"] == "before re-transcribe"
        rows = db.get_line_history_snapshot(history[0]["id"])
        assert [r["zh"] for r in rows] == ["原文"]
        assert [ln.zh for ln in db.load_line_objects(did)] == ["真实台词"]


# ---------------------------------------------------------------------------
# From TestResegmentGuardrail and TestResegmentationStaleSnapshotSafety
# ---------------------------------------------------------------------------

def _reseg_drama(translated=True):
    did = db.create_drama(title_en="R", media_type="audio_drama", content_mode="audio_drama",
                          status="translated", source_language="zh")
    db.save_lines(did, [
        Line(idx=0, start=0.0, end=2.0, zh="你好。", en="Hello." if translated else "",
             flag="needs_review" if translated else None),
        Line(idx=1, start=2.0, end=12.0, zh=LONG,
             en="He said he'd come tomorrow..." if translated else "",
             flag="mistranslation" if translated else None),
        Line(idx=2, start=12.0, end=14.0, zh="再见。", en="Bye." if translated else ""),
    ])
    if translated:
        db.save_translation_notes(did, [
            {"line_idx": 0, "term": "你好", "note_type": "cultural", "note": "greeting"},
            {"line_idx": 1, "term": "明天", "note_type": "cultural", "note": "tomorrow"},
        ])
    return did


class TestResegmentInvariants:
    def test_translated_drama_needs_confirm_then_clears_only_the_split_line(self):
        did = _reseg_drama()
        before = db.load_line_objects(did)
        ids = [ln.id for ln in before]
        preview = restructure_service.preview_resegmentation(did)
        assert preview["needs_confirm"] is True
        with pytest.raises(InvalidInputError):
            restructure_service.start_resegmentation(did, ids)
        assert _snapshot(did) == [(ln.id, ln.zh, ln.en) for ln in before]

        job = _wait(restructure_service.start_resegmentation(did, ids, confirm=True)["job_id"])
        assert job["status"] == "done", job
        after = db.load_line_objects(did)
        assert [ln.zh for ln in after] == ["你好。", FIRST, SECOND, "再见。"]
        assert (after[0].id, after[0].en, after[0].flag) == (ids[0], "Hello.", "needs_review")
        assert (after[3].id, after[3].en) == (ids[2], "Bye.")
        assert [(ln.en, ln.flag) for ln in after[1:3]] == [("", None), ("", None)]
        assert [n["term"] for n in db.list_translation_notes(did)] == ["你好"]
        assert any(h["label"] == "before re-segment" for h in db.list_line_history(did))

    def test_untranslated_drama_needs_no_confirmation(self):
        did = _reseg_drama(translated=False)
        ids = [ln.id for ln in db.load_line_objects(did)]
        assert restructure_service.preview_resegmentation(did)["needs_confirm"] is False
        job = _wait(restructure_service.start_resegmentation(did, ids)["job_id"])
        assert job["status"] == "done", job

    def test_preview_reads_the_database_not_a_client_snapshot(self):
        did = _reseg_drama(translated=False)
        _append_line(did)
        preview = restructure_service.preview_resegmentation(did)
        assert preview["source_line_ids"] == [ln.id for ln in db.load_line_objects(did)]

    def test_apply_refuses_when_lines_changed_since_preview(self):
        did = _reseg_drama(translated=False)
        seen = restructure_service.preview_resegmentation(did)["source_line_ids"]
        _append_line(did)
        before = _snapshot(did)
        with pytest.raises(ConflictError):
            restructure_service.start_resegmentation(did, seen)
        assert _snapshot(did) == before
        assert db.list_line_history(did) == []

    def test_apply_succeeds_when_nothing_changed_since_preview(self):
        did = _reseg_drama(translated=False)
        seen = restructure_service.preview_resegmentation(did)["source_line_ids"]
        job = _wait(restructure_service.start_resegmentation(did, seen)["job_id"])
        assert job["status"] == "done", job
        assert len(db.load_line_objects(did)) == 4

    def test_a_per_line_edit_made_while_the_job_runs_is_kept(self, monkeypatch):
        """The job commits a full sync from lines loaded at start; an edit
        to an unsplit line landing mid-job must survive (db.save_lines
        writes only fields that differ from each line's loaded `orig`)."""
        from services import lines_service
        did = _reseg_drama(translated=False)
        ids = [ln.id for ln in db.load_line_objects(did)]
        real = resegment.resegment_lines

        def slow(*a, **k):
            lines_service.patch_line(did, ids[2], en="user edit during job")
            return real(*a, **k)
        monkeypatch.setattr(resegment, "resegment_lines", slow)
        job = _wait(restructure_service.start_resegmentation(did, ids)["job_id"])
        assert job["status"] == "done", job
        after = db.load_line_objects(did)
        assert (after[-1].id, after[-1].en) == (ids[2], "user edit during job")


# ---------------------------------------------------------------------------
# From TestMergeAndRestoreStaleIdSetSafety and TestMergePreviewDoesNotMutateLiveLines
# ---------------------------------------------------------------------------

def _two_lines():
    did = db.create_drama(title_en="M", media_type="audio_drama", content_mode="audio_drama",
                          status="translated", source_language="zh")
    db.save_lines(did, [Line(idx=0, start=0.0, end=0.5, zh="你", en="You"),
                        Line(idx=1, start=0.6, end=1.0, zh="好", en="good")])
    return did, [ln.id for ln in db.load_line_objects(did)]


class TestMergeAndRestoreInvariants:
    def test_merge_refuses_when_lines_changed_since_loaded(self):
        did, ids = _two_lines()
        _append_line(did)
        before = _snapshot(did)
        with pytest.raises(ConflictError):
            restructure_service.merge_lines(did, ids, ids)
        assert _snapshot(did) == before and db.list_line_history(did) == []

    def test_merge_succeeds_when_nothing_changed(self):
        did, ids = _two_lines()
        restructure_service.merge_lines(did, ids, ids)
        assert [ln.zh for ln in db.load_line_objects(did)] == ["你好"]

    def test_a_rejected_merge_leaves_lines_untouched(self):
        """No preview step in the API; the nearest invariant is that a
        merge that fails validation writes nothing."""
        did, ids = _two_lines()
        before = _snapshot(did)
        with pytest.raises(InvalidInputError):
            restructure_service.merge_lines(did, list(reversed(ids)), ids)
        assert _snapshot(did) == before and db.list_line_history(did) == []

    def test_restore_refuses_when_lines_changed_since_loaded(self):
        did, ids = _two_lines()
        db.save_line_history_snapshot(did, db.load_line_objects(did), "two lines")
        _append_line(did)
        before = _snapshot(did)
        hid = db.list_line_history(did)[0]["id"]
        with pytest.raises(ConflictError):
            restructure_service.restore_version(did, hid, ids)
        assert _snapshot(did) == before

    def test_restore_succeeds_when_nothing_changed(self):
        did, ids = _two_lines()
        db.save_line_history_snapshot(did, db.load_line_objects(did), "two lines")
        hid = db.list_line_history(did)[0]["id"]
        merged = restructure_service.merge_lines(did, ids, ids)
        restructure_service.restore_version(did, hid, merged["line_ids"])
        assert [ln.zh for ln in db.load_line_objects(did)] == ["你", "好"]


# ---------------------------------------------------------------------------
# From TestDeleteDramaBlockedByRunningJob / TestDestructiveActionsNeedConfirmation
# ---------------------------------------------------------------------------

class TestDeleteDramaInvariants:
    def test_delete_refused_without_confirmation(self):
        did = db.create_drama(title_en="X", source_language="zh")
        with pytest.raises(InvalidInputError):
            drama_service.delete_drama(did, confirm=True, confirm_text="delete")
        assert db.get_drama(did) is not None

    def test_delete_refused_while_a_job_runs_even_when_confirmed(self, monkeypatch):
        did = db.create_drama(title_en="X", source_language="zh")
        monkeypatch.setattr(background_jobs, "any_job_running_for_drama", lambda d: d == did)
        with pytest.raises(ConflictError):
            drama_service.delete_drama(did, confirm=True, confirm_text="DELETE")
        assert db.get_drama(did) is not None


# ---------------------------------------------------------------------------
# From TestDubGenerationRealMidRunStop::test_done_job_result_is_applied_via_field_scoped_save
# ---------------------------------------------------------------------------

class TestDubResultInvariants:
    def test_done_result_touches_only_dub_filename(self):
        from services import dub_service
        did = db.create_drama(title_en="D", source_language="zh", content_mode="audio_drama")
        db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="a", en="A")])
        job_copy = db.load_line_objects(did)
        # the user edits the line while the dub job runs
        live = db.load_line_objects(did)
        live[0].en, live[0].flag = "edited", "idiom"
        db.save_lines(did, live, fields=("en", "flag"))
        job_copy[0].dub_filename = "line_0.mp3"
        dub_service.apply_dub_result(did, {"lines": job_copy})
        [ln] = db.load_line_objects(did)
        assert (ln.en, ln.flag, ln.dub_filename) == ("edited", "idiom", "line_0.mp3")


# ---------------------------------------------------------------------------
# From TestNewDramaLanguageSelector
# ---------------------------------------------------------------------------

class TestCreateDramaInvariants:
    def test_no_silent_zh_default_for_source_language(self):
        with pytest.raises(InvalidInputError):
            drama_service.create_drama(source_language=None, title_en="X")
        detail = drama_service.create_drama(source_language="ja", title_en="X")
        assert db.get_drama(detail["id"])["source_language"] == "ja"
