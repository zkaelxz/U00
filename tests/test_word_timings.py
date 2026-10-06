"""Whisper word timings stored per line (lines.word_timings) and used by the
re-split paths. Fake words only: no Whisper, no audio, no GPU."""
import contextlib
import json
import os
import sqlite3
import time

import pytest

import background_jobs
import core
import db
import resegment
from core import Line
from services import restructure_service as svc
from services import review_lines_service

# The owner's case: about 17 s and 56 Chinese characters with no punctuation,
# spoken as four phrases with a real pause between each.
PHRASES = ["我今天早上起来以后先去公园走", "跑了半个小时然后回家洗澡换上", "干净衣服吃了一碗热乎乎面条再",
           "出门坐地铁去公司上班开会了吧"]
TEXT = "".join(PHRASES)
START = 100.0
WORD_S, INNER_GAP, PAUSE = 0.5, 0.05, 0.6


def _owner_words():
    """Two-character words; 0.05 s between words in a phrase, 0.6 s between phrases."""
    words, t = [], START
    for n, phrase in enumerate(PHRASES):
        if n:
            t += PAUSE - INNER_GAP
        for k in range(0, len(phrase), 2):
            words.append({"word": phrase[k:k + 2], "start": round(t, 3),
                          "end": round(t + WORD_S, 3)})
            t += WORD_S + INNER_GAP
    return words


WORDS = _owner_words()
END = WORDS[-1]["end"]


def _phrase_times():
    out, i = [], 0
    for phrase in PHRASES:
        n = len(phrase) // 2
        out.append((WORDS[i]["start"], WORDS[i + n - 1]["end"]))
        i += n
    return out


@pytest.fixture(autouse=True)
def _env(isolated_db, monkeypatch):
    background_jobs.clear_all_jobs()
    monkeypatch.setattr(resegment, "word_boundaries", lambda *a, **k: None)
    yield
    background_jobs.clear_all_jobs()


def _seed(words=True, text=TEXT, extra=()):
    did = db.create_drama(title_zh="D", source_language="zh")
    payload = core.encode_line_words(text, WORDS) if words else None
    db.save_lines(did, [Line(idx=0, start=START, end=END, zh=text, speaker="A",
                             word_timings=payload), *extra])
    return did, [r["id"] for r in db.load_lines(did)]


def _stored(did):
    with contextlib.closing(sqlite3.connect(db.DB_PATH)) as c:
        return [r[0] for r in c.execute(
            "SELECT word_timings FROM lines WHERE drama_id = ? ORDER BY idx, id", (did,))]


def _valid_words(did):
    return [core.line_words(ln) for ln in db.load_line_objects(did, with_words=True)]


# ---- encode / decode -----------------------------------------------------------

class TestPayload:
    def test_round_trip_is_offsets_and_ms_not_the_words_again(self):
        payload = core.encode_line_words(TEXT, WORDS)
        data = json.loads(payload)
        assert data["h"] == core.text_fingerprint(TEXT)
        assert data["w"][0] == [0, 2, 100000, 100500]
        assert PHRASES[0][:2] not in payload
        ln = Line(idx=0, start=START, end=END, zh=TEXT, word_timings=payload)
        assert core.line_words(ln) == [{"word": w["word"], "start": w["start"], "end": w["end"]}
                                       for w in WORDS]

    def test_words_that_do_not_spell_the_text_are_not_stored(self):
        assert core.encode_line_words(TEXT + "啊", WORDS) is None
        assert core.encode_line_words(TEXT, WORDS[:-1]) is None
        assert core.encode_line_words(TEXT, []) is None

    def test_any_text_edit_invalidates(self):
        payload = core.encode_line_words(TEXT, WORDS)
        for edited in (TEXT[:-1] + "啦", TEXT + " ", "x" + TEXT[1:]):
            assert core.line_words(Line(idx=0, start=START, end=END, zh=edited,
                                        word_timings=payload)) is None

    def test_retimed_line_keeps_words_but_unrelated_audio_does_not(self):
        payload = core.encode_line_words(TEXT, WORDS)
        assert core.line_words(Line(idx=0, start=START + 0.3, end=END - 0.2, zh=TEXT,
                                    word_timings=payload))
        assert core.line_words(Line(idx=0, start=500.0, end=520.0, zh=TEXT,
                                    word_timings=payload)) is None

    @pytest.mark.parametrize("bad", ["", "{", "[]", '{"h":"x"}', '{"h":"%s","w":[[0,2]]}',
                                     '{"h":"%s","w":[[2,4,1,2],[0,2,1,2]]}',
                                     '{"h":"%s","w":[[0,999,1,2]]}', '{"h":"%s","w":"no"}'])
    def test_malformed_payloads_are_ignored(self, bad):
        payload = bad.replace("%s", core.text_fingerprint(TEXT))
        assert core.line_words(Line(idx=0, start=START, end=END, zh=TEXT,
                                    word_timings=payload)) is None

    def test_cap_on_word_count_and_bytes(self):
        text = "字" * (core.MAX_STORED_WORDS + 1)
        words = [{"word": "字", "start": k * 0.1, "end": k * 0.1 + 0.05} for k in range(len(text))]
        assert core.encode_line_words(text, words) is None
        ok = core.encode_line_words(text[:-1], words[:-1])
        assert ok is not None and len(ok) <= core.MAX_STORED_WORD_BYTES
        huge = '{"h":"%s","w":[]}' % core.text_fingerprint(TEXT) + " " * core.MAX_STORED_WORD_BYTES
        assert core.line_words(Line(idx=0, start=START, end=END, zh=TEXT,
                                    word_timings=huge)) is None

    def test_a_100k_character_line_never_makes_a_large_row(self):
        text = "字" * 100_000
        words = [{"word": "字", "start": k * 0.01, "end": k * 0.01 + 0.005}
                 for k in range(len(text))]
        t0 = time.monotonic()
        assert core.encode_line_words(text, words) is None
        assert time.monotonic() - t0 < 0.5
        did = db.create_drama(title_zh="D")
        db.save_lines(did, [Line(idx=0, start=0.0, end=1000.0, zh=text,
                                 word_timings=core.encode_line_words(text, words))])
        assert _stored(did) == [None]
        t0 = time.monotonic()
        r = svc.resplit_long_lines(did, [r["id"] for r in db.load_lines(did)], dry_run=True)
        assert r["split_lines"] == 0 and time.monotonic() - t0 < 2.0


# ---- storage -----------------------------------------------------------------

class TestStorage:
    def test_list_and_api_never_carry_the_column(self):
        did, ids = _seed()
        assert _stored(did)[0]
        assert "word_timings" not in db.load_lines(did)[0]
        ln = db.load_line_objects(did)[0]
        assert ln.word_timings is None and "word_timings" not in ln.orig
        assert "word_timings" not in review_lines_service.line_dict(
            db.load_line_objects(did, with_words=True)[0])
        assert "word_timings" not in repr(db.load_line_objects(did, with_words=True)[0])
        snap = db.list_line_history(did)
        assert all("word" not in json.dumps(s) for s in snap)

    def test_schemas_have_no_word_field(self):
        import api.schemas as schemas
        import pydantic
        for name in dir(schemas):
            model = getattr(schemas, name)
            if isinstance(model, type) and issubclass(model, pydantic.BaseModel):
                assert not any("word_tim" in f for f in model.model_fields), name

    def test_saves_that_do_not_load_words_never_wipe_them(self):
        did, ids = _seed()
        lines = db.load_line_objects(did)
        lines[0].en, lines[0].speaker = "translated", "B"
        db.save_lines(did, lines)                       # full sync without words loaded
        db.save_lines(did, lines, fields=("en",))
        lines[0].start = START + 0.2                    # a re-time keeps them
        db.save_lines(did, lines, fields=("start",))
        assert _valid_words(did)[0]

    def test_field_scoped_saves_never_write_words(self):
        did, ids = _seed(words=False)
        lines = db.load_line_objects(did)
        lines[0].word_timings, lines[0].en = core.encode_line_words(TEXT, WORDS), "x"
        db.save_lines(did, lines, fields=("en",))
        assert _stored(did) == [None]

    def test_text_edit_clears_them_on_every_write_path(self):
        did, ids = _seed()
        lines = db.load_line_objects(did, with_words=True)
        lines[0].zh = TEXT[:-1]
        db.save_lines(did, lines)
        assert _stored(did) == [None] and lines[0].word_timings is None
        db.save_lines(did, lines)                       # a second save can't put them back
        assert _stored(did) == [None]

        did, ids = _seed()
        lines = db.load_line_objects(did)
        lines[0].zh = TEXT[:-1]
        db.save_lines(did, lines, fields=("zh",), only_if_unchanged=True)
        assert _stored(did) == [None]

        did, ids = _seed()
        assert db.update_line_fields_if(did, ids[0], {"zh": "new"}, {"zh": TEXT})
        assert _stored(did) == [None]

    def test_cas_writing_the_same_text_or_other_fields_keeps_them(self):
        did, ids = _seed()
        assert db.update_line_fields_if(did, ids[0], {"zh": TEXT}, {"zh": TEXT})
        assert db.update_line_fields_if(did, ids[0], {"start": START + 0.1}, {})
        assert _valid_words(did)[0]

    def test_unchanged_full_sync_does_not_rewrite_them(self, monkeypatch):
        did, ids = _seed()
        statements = []
        real = db.get_conn

        def traced():
            conn = real()
            conn.set_trace_callback(statements.append)
            return conn
        monkeypatch.setattr(db, "get_conn", traced)
        db.save_lines(did, db.load_line_objects(did, with_words=True))
        assert not [s for s in statements if s.startswith("UPDATE lines")]

    def test_deleted_with_their_line_and_drama(self):
        did, ids = _seed(extra=[Line(idx=1, start=END, end=END + 1, zh="好")])
        svc.delete_line(did, ids[0], ids, confirm=True)
        assert _stored(did) == [None]
        did, ids = _seed()
        db.delete_drama(did)
        with contextlib.closing(sqlite3.connect(db.DB_PATH)) as c:
            assert c.execute("SELECT COUNT(*) FROM lines WHERE word_timings IS NOT NULL"
                             ).fetchone()[0] == 0


# ---- re-split (sensitivity presets) ---------------------------------------------

class TestResplit:
    def test_owner_case_cuts_at_real_pauses_with_real_times(self):
        did, ids = _seed()
        dry = svc.resplit_long_lines(did, ids, dry_run=True)
        r = svc.resplit_long_lines(did, ids)
        assert (dry["split_lines"], dry["pieces"]) == (1, 4)
        assert (r["split_lines"], r["line_count"]) == (1, dry["line_count"])
        rows = db.load_lines(did)
        assert [x["zh"] for x in rows] == PHRASES
        assert [(x["start"], x["end"]) for x in rows] == pytest.approx(_phrase_times())
        assert rows[0]["id"] == ids[0]
        # every piece got exactly its own words, so it can be cut again
        words = _valid_words(did)
        assert all(words) and [len(w) for w in words] == [len(p) // 2 for p in PHRASES]

    def test_pieces_can_be_split_again_with_their_words(self):
        did, ids = _seed()
        svc.resplit_long_lines(did, ids, max_seconds=10)
        rows = db.load_lines(did)
        assert [x["zh"] for x in rows] == [PHRASES[0] + PHRASES[1], PHRASES[2] + PHRASES[3]]
        r = svc.resplit_long_lines(did, [x["id"] for x in rows])
        assert r["split_lines"] == 2
        rows = db.load_lines(did)
        assert [x["zh"] for x in rows] == PHRASES
        assert [(x["start"], x["end"]) for x in rows] == pytest.approx(_phrase_times())
        assert all(_valid_words(did))

    def test_no_pause_long_enough_means_no_cut(self):
        did, ids = _seed()
        svc.resplit_long_lines(did, ids)
        ids = [r["id"] for r in db.load_lines(did)]
        # 3.8 s phrases over a 2 s cap, but only 0.05 s between their words
        assert svc.resplit_long_lines(did, ids, sensitivity="more", max_seconds=2)[
            "split_lines"] == 0

    def test_line_without_words_behaves_as_before(self):
        did, ids = _seed(words=False)
        r = svc.resplit_long_lines(did, ids)
        assert r["split_lines"] == 0 and "none has a sentence or comma break" in r["note"]

    def test_stale_words_after_an_edit_are_not_used(self):
        did, ids = _seed()
        edited = TEXT[:-1] + "啦"
        db.update_line_fields_if(did, ids[0], {"zh": edited}, {"zh": TEXT})
        # even a payload left behind by some other writer is refused by its fingerprint
        with contextlib.closing(sqlite3.connect(db.DB_PATH)) as c:
            c.execute("UPDATE lines SET word_timings = ? WHERE id = ?",
                      (core.encode_line_words(TEXT, WORDS), ids[0]))
            c.commit()
        assert svc.resplit_long_lines(did, ids)["split_lines"] == 0
        assert db.load_lines(did)[0]["zh"] == edited

    def test_cuts_stay_inside_a_retimed_line(self):
        did, ids = _seed()
        db.update_line_fields_if(did, ids[0], {"start": START + 0.2, "end": END - 0.3}, {})
        svc.resplit_long_lines(did, ids)
        rows = db.load_lines(did)
        assert rows[0]["start"] == pytest.approx(START + 0.2)
        assert rows[-1]["end"] == pytest.approx(END - 0.3)
        assert [x["zh"] for x in rows] == PHRASES


# ---- AI / rules re-segmentation and split_times -----------------------------------

class TestResegment:
    def test_split_times_use_word_starts_when_valid(self):
        ln = Line(idx=0, start=START, end=END, zh=TEXT,
                  word_timings=core.encode_line_words(TEXT, WORDS))
        times = _phrase_times()
        assert resegment.split_times(ln, PHRASES) == pytest.approx([t[0] for t in times[1:]])
        # a cut inside a word can't use them
        mid = [TEXT[:3], TEXT[3:]]
        assert resegment.split_times(ln, mid) == resegment.split_times(
            Line(idx=0, start=START, end=END, zh=TEXT), mid)

    def test_pause_tier_cuts_a_line_with_no_punctuation(self):
        ln = Line(idx=0, start=START, end=END, zh=TEXT, id=7,
                  word_timings=core.encode_line_words(TEXT, WORDS))
        no_segmenter = lambda *a: None  # noqa: E731
        new, changed = resegment.resegment_lines([ln], "zh", max_chars=30,
                                                 boundaries_fn=no_segmenter)
        assert [x.zh for x in new] == [PHRASES[0] + PHRASES[1], PHRASES[2] + PHRASES[3]]
        assert new[1].start == pytest.approx(_phrase_times()[2][0])
        assert all(core.line_words(x) for x in new)
        bare, changed = resegment.resegment_lines(
            [Line(idx=0, start=START, end=END, zh=TEXT, id=7)], "zh", max_chars=30,
            boundaries_fn=no_segmenter)
        assert len(bare) == 1 and not changed

    def test_preview_matches_apply(self):
        did, ids = _seed()
        monkey_cap = resegment.max_line_chars
        try:
            resegment.max_line_chars = lambda language: 30
            preview = svc.preview_resegmentation(did)
            job = svc.start_resegmentation(did, ids, confirm=True)
            _wait(job["job_id"])
        finally:
            resegment.max_line_chars = monkey_cap
        assert [p for c in preview["changed"] for p in c["pieces"]] == [
            x["zh"] for x in db.load_lines(did)]
        assert all(_valid_words(did))


def _wait(job_id):
    for _ in range(200):
        job = background_jobs.get_status(job_id)
        if job and job["status"] not in ("running", "queued"):
            assert job["status"] == "done", job
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


# ---- Review split and merge --------------------------------------------------------

class TestSplitAndMerge:
    def test_split_at_a_word_boundary_partitions_words_and_uses_real_time(self):
        did, ids = _seed()
        at = len(PHRASES[0])
        svc.split_line(did, ids[0], ids, at_char=at, expected_zh=TEXT)
        rows = db.load_lines(did)
        assert rows[0]["end"] == rows[1]["start"] == pytest.approx(_phrase_times()[1][0])
        a, b = _valid_words(did)
        assert len(a) + len(b) == len(WORDS) and a[-1]["word"] == WORDS[len(a) - 1]["word"]
        # the second piece splits again on its own words
        ids = [r["id"] for r in rows]
        svc.split_line(did, ids[1], ids, at_char=len(PHRASES[1]), expected_zh=rows[1]["zh"])
        assert db.load_lines(did)[2]["start"] == pytest.approx(_phrase_times()[2][0])
        assert all(_valid_words(did))

    def test_split_inside_a_word_drops_words_and_estimates(self):
        did, ids = _seed()
        svc.split_line(did, ids[0], ids, at_char=3, expected_zh=TEXT)
        rows = db.load_lines(did)
        assert rows[0]["end"] == pytest.approx(START + (END - START) * 3 / len(TEXT))
        assert _stored(did) == [None, None]

    def test_typed_time_is_kept_and_words_still_partitioned(self):
        did, ids = _seed()
        svc.split_line(did, ids[0], ids, at_char=len(PHRASES[0]), expected_zh=TEXT,
                       at_time=START + 1.0)
        assert db.load_lines(did)[0]["end"] == START + 1.0
        assert all(_valid_words(did))

    def test_merge_joins_valid_words_and_drops_otherwise(self):
        did, ids = _seed()
        svc.split_line(did, ids[0], ids, at_char=len(PHRASES[0]), expected_zh=TEXT)
        ids = [r["id"] for r in db.load_lines(did)]
        svc.merge_lines(did, ids, ids)
        [words] = _valid_words(did)
        assert db.load_lines(did)[0]["zh"] == TEXT and words == core.line_words(
            Line(idx=0, start=START, end=END, zh=TEXT,
                 word_timings=core.encode_line_words(TEXT, WORDS)))

        did, ids = _seed(extra=[Line(idx=1, start=END, end=END + 1, zh="好的")])
        svc.merge_lines(did, ids, ids)
        assert _stored(did) == [None]

    def test_undo_restores_text_without_stale_words(self):
        did, ids = _seed()
        svc.split_line(did, ids[0], ids, at_char=len(PHRASES[0]), expected_zh=TEXT)
        history = db.list_line_history(did)[0]["id"]
        svc.restore_version(did, history, [r["id"] for r in db.load_lines(did)])
        assert db.load_lines(did)[0]["zh"] == TEXT
        assert _valid_words(did) == [None]


# ---- transcription and backups -----------------------------------------------------

def test_transcription_stores_words_with_the_lines_it_creates(monkeypatch):
    from services import transcribe_service
    did = db.create_drama(title_en="D", audio_filename="audio.wav", transcript_mode="whisper")
    ddir = db.drama_dir(did)
    os.makedirs(ddir, exist_ok=True)
    open(os.path.join(ddir, "audio.wav"), "wb").close()
    job_id = f"transcribe_{did}"
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}
    monkeypatch.setattr(core, "is_whisper_model_cached", lambda size: True)
    monkeypatch.setattr(core, "load_whisper_model", lambda *a, **k: None)
    monkeypatch.setattr(core, "get_whisper_device_info",
                        lambda *a, **k: {"device": "cpu", "compute_type": "int8"})
    monkeypatch.setattr(transcribe_service, "transcribe_for_timing", lambda *a, **k: [
        {"start": START, "end": END, "text": TEXT, "words": WORDS},
        {"start": END + 1, "end": END + 2, "text": "好的", "words": [
            {"word": "好", "start": END + 1, "end": END + 1.4}]}])   # words don't spell it

    transcribe_service._run_transcribe_and_apply_job(
        job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
        "medium", 5, 300, 0.5, False, "auto", False, False, False, None, None, None,
        use_gpu=False)

    # the transcription's own 8 s split already cuts at the pauses
    assert [x["zh"] for x in db.load_lines(did)] == PHRASES + ["好的"]
    stored = _stored(did)
    assert all(stored[:-1]) and stored[-1] is None
    assert all(_valid_words(did)[:-1])
    result = json.dumps(background_jobs.get_status(job_id)["result"])
    assert '"words"' not in result and "word_timings" not in result and "100.5" not in result


def test_backup_import_of_an_older_backup_without_the_column(tmp_path):
    from services import backup_import_service as bis
    from services import library_admin_service as las
    did, ids = _seed()
    with contextlib.closing(sqlite3.connect(db.DB_PATH)) as c:
        c.execute("ALTER TABLE lines DROP COLUMN word_timings")
        c.commit()
    dest = os.path.join(db.LIBRARY_DIR, "old.zip")
    las.write_backup_zip(dest, include_media=False)
    with contextlib.closing(sqlite3.connect(db.DB_PATH)) as c:
        c.execute("ALTER TABLE lines ADD COLUMN word_timings TEXT")
        c.commit()
    with open(dest, "rb") as fh:
        import io
        res = bis.import_dramas(io.BytesIO(fh.read()), [did], confirm=True,
                                confirm_text="RESTORE")
    new = res["imported"][0]["drama_id"]
    assert [r["zh"] for r in db.load_lines(new)] == [TEXT] and _stored(new) == [None]


def test_backup_copy_keeps_words_but_drops_oversized_ones():
    import io
    from services import backup_import_service as bis
    from services import library_admin_service as las
    did, ids = _seed()
    big, big_ids = _seed()
    with contextlib.closing(sqlite3.connect(db.DB_PATH)) as c:
        c.execute("UPDATE lines SET word_timings = ? WHERE id = ?",
                  ("x" * (core.MAX_STORED_WORD_BYTES + 1), big_ids[0]))
        c.commit()
    dest = os.path.join(db.LIBRARY_DIR, "b.zip")
    las.write_backup_zip(dest, include_media=False)
    with open(dest, "rb") as fh:
        res = bis.import_dramas(io.BytesIO(fh.read()), [did, big], confirm=True,
                                confirm_text="RESTORE")
    new, new_big = (i["drama_id"] for i in res["imported"])
    assert _valid_words(new)[0] and _stored(new_big) == [None]
