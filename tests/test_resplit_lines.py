"""Re-split over-long lines in place (services/restructure_service.resplit_long_lines and
POST /api/restructure/dramas/{id}/resplit). Mocked: no GPU, no aligner model, no audio."""
import os
import subprocess
import threading
import time

import pytest

import background_jobs
import db
import diarize
from core import Line
from services import restructure_service as svc
from services.service_errors import ConflictError, InvalidInputError

SENT = "我今天早上很早就起床了然后去公园跑步。"  # 19 CJK chars
LONG = SENT * 3  # 57 chars, 30 s: over both limits
SHORT = "你好。"


@pytest.fixture(autouse=True)
def _env(isolated_db):
    background_jobs.clear_all_jobs()
    yield
    background_jobs.clear_all_jobs()


def _seed(en="", manual=False, audio=True):
    did = db.create_drama(title_zh="D", source_language="zh",
                          audio_filename="audio.wav" if audio else None)
    if audio:
        with open(os.path.join(db.drama_dir(did), "audio.wav"), "wb") as f:
            f.write(b"x")
    db.save_lines(did, [
        Line(idx=0, start=0.0, end=2.0, zh=SHORT, speaker="A"),
        Line(idx=1, start=2.0, end=32.0, zh=LONG, en=en, speaker="B", speaker_manual=manual,
             flag="unsure", flag_note="n"),
        Line(idx=2, start=32.0, end=34.0, zh=SHORT, speaker="A")])
    return did, [r["id"] for r in db.load_lines(did)]


def _wait(job_id):
    for _ in range(200):
        job = background_jobs.get_status(job_id)
        if job and job["status"] not in ("running", "queued"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_proportional_split_in_place_keeps_parent_id_flag_speaker():
    did, ids = _seed()
    r = svc.resplit_long_lines(did, ids)
    assert (r["split_lines"], r["lines_before"], r["line_count"]) == (1, 3, 5)
    assert r["timing"] == "proportional" and r["speakers_reassigned"] is False
    rows = db.load_lines(did)
    assert len(rows) == 5 and rows[1]["id"] == ids[1] and rows[1]["flag"] == "unsure"
    assert [x["zh"] for x in rows[1:4]] == [SENT] * 3
    assert [x["speaker"] for x in rows[1:4]] == ["B"] * 3
    assert rows[1]["start"] == 2.0 and rows[3]["end"] == 32.0
    assert all(a["end"] == pytest.approx(b["start"]) for a, b in zip(rows[1:3], rows[2:4]))
    assert any(h["label"] == "before re-split" for h in db.list_line_history(did))


def test_translated_long_line_needs_confirm_then_clears_en():
    did, ids = _seed(en="old translation")
    with pytest.raises(InvalidInputError):
        svc.resplit_long_lines(did, ids)
    assert len(db.load_lines(did)) == 3
    r = svc.resplit_long_lines(did, ids, confirm=True)
    assert r["cleared_translations"] == 1
    assert [x["en"] for x in db.load_lines(did)[1:4]] == ["", "", ""]


def test_saved_turns_relabel_pieces_and_keep_manual():
    did, ids = _seed()
    diarize.save_turns(db.drama_dir(did), [{"start": 0.0, "end": 12.0, "speaker": "S1"},
                                           {"start": 12.0, "end": 40.0, "speaker": "S2"}])
    r = svc.resplit_long_lines(did, ids)
    assert r["speakers_reassigned"] is True
    rows = db.load_lines(did)
    assert [x["speaker"] for x in rows[1:4]] == ["S1", "S2", "S2"]
    # unsplit lines keep their speakers even where the saved turns disagree
    assert (rows[0]["speaker"], rows[4]["speaker"]) == ("A", "A")
    did, ids = _seed(manual=True)
    diarize.save_turns(db.drama_dir(did), [{"start": 0.0, "end": 40.0, "speaker": "S1"}])
    svc.resplit_long_lines(did, ids)
    assert [x["speaker"] for x in db.load_lines(did)[1:4]] == ["B"] * 3


def test_nothing_to_split_and_stale_ids():
    did = db.create_drama(title_zh="D")
    db.save_lines(did, [Line(idx=0, start=0, end=2, zh=SHORT)])
    ids = [r["id"] for r in db.load_lines(did)]
    assert svc.resplit_long_lines(did, ids)["split_lines"] == 0
    with pytest.raises(ConflictError):
        svc.resplit_long_lines(did, [999])


def _fake_aligner(monkeypatch, fn):
    import forced_align
    monkeypatch.setattr(forced_align, "align_with_qwen3", fn)


def test_align_job_uses_aligner_boundaries(monkeypatch):
    did, ids = _seed()
    seen = {}

    def align(audio, texts, segs, language, use_gpu=False):
        seen.update(texts=texts, segs=segs, audio=os.path.basename(audio))
        return [Line(idx=i, start=2.0 + 9 * i + 1, end=2.0 + 9 * (i + 1), zh=t)
                for i, t in enumerate(texts)]
    _fake_aligner(monkeypatch, align)
    r = svc.resplit_long_lines(did, ids, align_to_audio=True)
    assert r["job_id"] == f"resplit_{did}"
    job = _wait(r["job_id"])
    assert job["status"] == "done", job
    res = job["result"]
    assert res["timing"] == "aligned" and res["aligned_lines"] == 1 and res["split_lines"] == 1
    assert seen["texts"] == [SENT] * 3 and seen["audio"] == "audio.wav"
    rows = db.load_lines(did)
    assert [x["start"] for x in rows[1:4]] == [3.0, 12.0, 21.0]


def test_align_falls_back_to_proportional_when_aligner_missing(monkeypatch):
    did, ids = _seed()

    def align(*a, **k):
        raise ImportError("No module named 'qwen_asr'")
    _fake_aligner(monkeypatch, align)
    r = svc.resplit_long_lines(did, ids, align_to_audio=True)
    res = _wait(r["job_id"])["result"]
    assert res["timing"] == "proportional" and "estimated timing" in res["note"]
    assert res["split_lines"] == 1 and len(db.load_lines(did)) == 5


@pytest.mark.parametrize("exc", [
    subprocess.CalledProcessError(1, ["ffmpeg", "-i", "/home/someone/lib/audio.wav"]),
    ImportError("cannot import name 'X' from 'qwen_asr' (C:\\Users\\someone\\site-packages)"),
])
def test_aligner_failure_note_has_no_paths(monkeypatch, exc):
    did, ids = _seed()

    def align(*a, **k):
        raise exc
    _fake_aligner(monkeypatch, align)
    res = _wait(svc.resplit_long_lines(did, ids, align_to_audio=True)["job_id"])["result"]
    assert "someone" not in res["note"] and "estimated timing" in res["note"]


def test_align_without_audio_splits_immediately_with_note():
    did, ids = _seed(audio=False)
    r = svc.resplit_long_lines(did, ids, align_to_audio=True)
    assert r["timing"] == "proportional" and "no stored audio" in r["note"]
    assert len(db.load_lines(did)) == 5


def test_repaired_alignment_flags_only_the_affected_piece(monkeypatch):
    did, ids = _seed()

    def align(audio, texts, segs, language, use_gpu=False):
        out = [Line(idx=i, start=3.0 + 9 * i, end=11.0 + 9 * i, zh=t) for i, t in enumerate(texts)]
        out[1].flag, out[1].flag_note = "timing_uncertain", "repaired"
        return out
    _fake_aligner(monkeypatch, align)
    job = _wait(svc.resplit_long_lines(did, ids, align_to_audio=True)["job_id"])
    assert job["status"] == "done", job
    rows = db.load_lines(did)
    assert [r["flag"] for r in rows[1:4]] == ["unsure", "timing_uncertain", None]
    assert rows[2]["flag_note"] == "repaired"


def test_bad_aligner_timing_keeps_that_line_proportional(monkeypatch):
    did, ids = _seed()
    _fake_aligner(monkeypatch, lambda audio, texts, segs, language, use_gpu=False: [
        Line(idx=i, start=5.0, end=4.0, zh=t) for i, t in enumerate(texts)])
    res = _wait(svc.resplit_long_lines(did, ids, align_to_audio=True)["job_id"])["result"]
    assert res["timing"] == "proportional" and "unusable" in res["note"]


def test_cancelled_job_writes_nothing(monkeypatch):
    did, ids = _seed()

    def align(audio, texts, segs, language, use_gpu=False):
        background_jobs.request_cancel(f"resplit_{did}")
        return [Line(idx=i, start=3.0 + i, end=4.0 + i, zh=t) for i, t in enumerate(texts)]
    _fake_aligner(monkeypatch, align)
    res = _wait(svc.resplit_long_lines(did, ids, align_to_audio=True)["job_id"])["result"]
    assert res["failed_reason"] == "cancelled" and len(db.load_lines(did)) == 3


def _edit(did, line_id, **fields):
    ln = next(x for x in db.load_line_objects(did) if x.id == line_id)
    for k, v in fields.items():
        setattr(ln, k, v)
    db.save_lines(did, [ln], fields=tuple(fields))


def _aligned(texts):
    return [Line(idx=i, start=2.0 + 9 * i + 1, end=2.0 + 9 * (i + 1), zh=t)
            for i, t in enumerate(texts)]


def test_job_started_during_alignment_blocks_the_commit(monkeypatch):
    did, ids = _seed()

    # Held open by an event, not a sleep: on a loaded runner a short sleep can
    # finish before the commit check runs and the job is no longer "running".
    release = threading.Event()

    def align(audio, texts, segs, language, use_gpu=False):
        background_jobs.start_job(f"translate_{did}", lambda: release.wait(30))
        return _aligned(texts)
    _fake_aligner(monkeypatch, align)
    try:
        res = _wait(svc.resplit_long_lines(did, ids, align_to_audio=True)["job_id"])["result"]
    finally:
        release.set()
    assert res["failed_reason"] == "not_applied" and "nothing was changed" in res["detail"]
    assert len(db.load_lines(did)) == 3
    _wait(f"translate_{did}")


@pytest.mark.parametrize("field,value,en", [("start", 2.5, ""), ("end", 31.0, ""),
                                            ("en", "new", "old")])
def test_timing_or_translation_edit_during_alignment_is_kept(monkeypatch, field, value, en):
    did, ids = _seed(en=en)

    def align(audio, texts, segs, language, use_gpu=False):
        _edit(did, ids[1], **{field: value})
        return _aligned(texts)
    _fake_aligner(monkeypatch, align)
    job_id = svc.resplit_long_lines(did, ids, align_to_audio=True, confirm=True)["job_id"]
    res = _wait(job_id)["result"]
    assert res["failed_reason"] == "not_applied" and "nothing was changed" in res["detail"]
    rows = db.load_lines(did)
    assert len(rows) == 3 and rows[1][field] == value


def test_cancelled_job_releases_the_aligner(monkeypatch):
    did, ids = _seed()
    db.save_lines(did, [*db.load_line_objects(did),
                        Line(idx=3, start=34.0, end=64.0, zh=LONG, speaker="C")])
    ids = [r["id"] for r in db.load_lines(did)]
    calls = {"align": 0, "release": 0}

    def align(audio, texts, segs, language, use_gpu=False):
        calls["align"] += 1
        background_jobs.request_cancel(f"resplit_{did}")
        return _aligned(texts)
    _fake_aligner(monkeypatch, align)
    monkeypatch.setattr(svc.core_module, "release_gpu_models",
                        lambda: calls.__setitem__("release", calls["release"] + 1))
    res = _wait(svc.resplit_long_lines(did, ids, align_to_audio=True)["job_id"])["result"]
    assert res["failed_reason"] == "cancelled" and len(db.load_lines(did)) == 4
    assert calls == {"align": 1, "release": 1}


def test_relabel_failure_after_commit_keeps_the_split(monkeypatch):
    did, ids = _seed()
    diarize.save_turns(db.drama_dir(did), [{"start": 0.0, "end": 40.0, "speaker": "S1"}])

    def boom(*a, **k):
        raise RuntimeError("disk error at /secret/path")
    monkeypatch.setattr(svc.diarization_service, "relabel_from_saved_turns", boom)
    r = svc.resplit_long_lines(did, ids)
    assert r["split_lines"] == 1 and r["speakers_reassigned"] is False
    assert r["note"].startswith("Split saved; speakers were not re-assigned:")
    assert "/secret" not in r["note"]
    rows = db.load_lines(did)
    assert len(rows) == 5 and [x["speaker"] for x in rows[1:4]] == ["B"] * 3


def test_refused_while_a_job_runs():
    did, ids = _seed()
    release = threading.Event()
    background_jobs.start_job(f"translate_{did}", lambda: release.wait(30))
    try:
        with pytest.raises(ConflictError):
            svc.resplit_long_lines(did, ids)
    finally:
        release.set()
        _wait(f"translate_{did}")


def test_route(isolated_db):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from api.api_config import ApiSettings
    from api.server import create_app
    did, ids = _seed()
    c = TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                   base_url="http://127.0.0.1:8600")
    bad = c.post(f"/api/restructure/dramas/{did}/resplit", json={"expected_line_ids": ids, "x": 1})
    assert bad.status_code == 422
    r = c.post(f"/api/restructure/dramas/{did}/resplit", json={"expected_line_ids": ids})
    assert r.status_code == 200, r.text
    assert r.json()["split_lines"] == 1 and r.json()["line_count"] == 5


# ---- per-speaker time summary from the saved turns ----

from services import diarization_service  # noqa: E402
from services.service_errors import NotFoundError  # noqa: E402


def test_speaker_time_summary_overlap_and_order(monkeypatch):
    did, _ = _seed()
    diarize.save_turns(db.drama_dir(did), [
        {"start": 0, "end": 10, "speaker": "A"}, {"start": 8, "end": 12, "speaker": "B"},
        {"start": 20, "end": 30, "speaker": "A"}, {"start": 5, "end": 5, "speaker": "C"}])
    from services import transcribe_service
    monkeypatch.setattr(transcribe_service, "_audio_duration_seconds", lambda p: 50.0)
    s = diarization_service.speaker_time_summary(did)
    assert [x["label"] for x in s["speakers"]] == ["A", "B"]
    assert s["speakers"][0] == {"label": "A", "seconds": 20.0, "percent": 83.3, "turns": 2}
    assert s["total_speech_seconds"] == 24.0
    assert s["uncovered_seconds"] == 28.0  # union is 0-12 and 20-30 = 22 of 50


def test_speaker_time_summary_none_without_turns_and_404():
    did, _ = _seed()
    assert diarization_service.speaker_time_summary(did) is None
    with pytest.raises(NotFoundError):
        diarization_service.speaker_time_summary(99999)


def test_config_route_carries_summary(isolated_db):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from api.api_config import ApiSettings
    from api.server import create_app
    did, _ = _seed(audio=False)
    c = TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                   base_url="http://127.0.0.1:8600")
    assert c.get(f"/api/diarization/dramas/{did}/config").json()["speaker_summary"] is None
    diarize.save_turns(db.drama_dir(did), [{"start": 0, "end": 4, "speaker": "A"}])
    body = c.get(f"/api/diarization/dramas/{did}/config").json()["speaker_summary"]
    assert body == {"speakers": [{"label": "A", "seconds": 4.0, "percent": 100.0, "turns": 1}],
                    "total_speech_seconds": 4.0, "uncovered_seconds": None}


def test_align_job_aligns_each_line_in_its_own_language(monkeypatch):
    did = db.create_drama(title_zh="D", source_language="zh", audio_filename="audio.wav")
    with open(os.path.join(db.drama_dir(did), "audio.wav"), "wb") as f:
        f.write(b"x")
    db.save_lines(did, [
        Line(idx=0, start=0.0, end=30.0, zh=LONG, lang="ja"),
        Line(idx=1, start=30.0, end=60.0, zh=LONG),
        Line(idx=2, start=60.0, end=90.0, zh=LONG, lang="en")])
    ids = [r["id"] for r in db.load_lines(did)]
    languages = []

    def align(audio, texts, segs, language, use_gpu=False):
        languages.append(language)
        if language == "en":  # the aligner doesn't cover English
            raise ValueError("unsupported language")
        return [Line(idx=i, start=segs[0]["start"] + 9 * i + 1, end=segs[0]["start"] + 9 * (i + 1), zh=t)
                for i, t in enumerate(texts)]
    _fake_aligner(monkeypatch, align)
    job = _wait(svc.resplit_long_lines(did, ids, align_to_audio=True)["job_id"])
    assert job["status"] == "done", job
    assert languages == ["ja", "zh", "en"]
    assert job["result"]["aligned_lines"] == 2  # the English line stays proportional


# ---- sensitivity presets, duration cap, dry run ----

BIG_ZH = ("今天早上我很早就起床了。然后我去了公园跑步，路上遇到了老朋友。他说最近工作很忙，所以没有时间锻炼。"
          "我们聊了很久！后来一起吃了早饭。「你好。我是小明。」他笑着说。我觉得今天过得很愉快。")
SAMPLES = {
    "zh": "我今天早上很早就起床了。然后去公园跑步。路上遇到了老朋友。我们聊了很久。后来一起吃了早饭。",
    "ja": "今日は朝とても早く起きました。それから公園に走りに行きました。道で古い友達に会いました。長い間話しました。その後一緒に朝ごはんを食べました。",
    "ko": "오늘 아침 일찍 일어났어요. 그리고 공원에 달리러 갔어요. 길에서 옛 친구를 만났어요. 오래 이야기했어요. 그 후에 같이 아침을 먹었어요.",
    "en": "I got up very early this morning. Then I went running in the park. I met an old friend on the way. "
          "We talked for a long time. Afterwards we had breakfast together.",
}


def _one_line(text, end, lang=None, source="zh", en=""):
    did = db.create_drama(title_zh="D", source_language=source)
    db.save_lines(did, [Line(idx=0, start=0.0, end=end, zh=text, lang=lang, en=en)])
    return did, [r["id"] for r in db.load_lines(did)]


def _count(text, end, source="zh", lang=None, **kw):
    did, ids = _one_line(text, end, lang=lang, source=source)
    return svc.resplit_long_lines(did, ids, dry_run=True, **kw)


def test_screenshot_like_line_splits_in_every_preset():
    # ~95 s, ~120 CJK characters, ten sentence ends
    for sens in ("normal", "more", "sentence"):
        r = _count(BIG_ZH, 95.0, sensitivity=sens)
        assert r["split_lines"] == 1 and r["pieces"] >= 6, (sens, r)


@pytest.mark.parametrize("lang", ["zh", "ja", "ko", "en"])
def test_presets_on_each_language(lang):
    text = SAMPLES[lang]
    assert _count(text, 20.0, source=lang)["split_lines"] == 1
    if lang in ("zh", "en"):  # ja/ko samples are over Normal's 40 characters anyway
        # 7 s is under Normal's 8 s limit but the text is over the per-language line length
        assert _count(text, 7.0, source=lang)["split_lines"] == 0
    more = _count(text, 7.0, source=lang, sensitivity="more")
    sent = _count(text, 7.0, source=lang, sensitivity="sentence")
    assert more["split_lines"] == 1 and sent["split_lines"] == 1
    assert sent["pieces"] >= more["pieces"]


def test_more_cuts_a_short_line_normal_leaves_alone():
    text = "我今天早上很早就起床了。然后去公园跑步。"  # 20 CJK chars, 6 s
    assert _count(text, 6.0)["split_lines"] == 0
    r = _count(text, 6.0, sensitivity="more")
    assert (r["split_lines"], r["pieces"]) == (1, 2)


def test_more_uses_each_lines_own_language_limit():
    text = "I got up early today. Then I went for a run."  # 44 chars: over en's 42
    assert _count(text, 6.0, source="zh", lang="en", sensitivity="more")["split_lines"] == 1
    assert _count(text, 6.0, source="en", lang="ko", sensitivity="more")["split_lines"] == 1
    assert _count("Hi there. Go on now.", 6.0, source="en", sensitivity="more")["split_lines"] == 0


def test_sentence_cuts_a_short_two_sentence_line_but_not_into_stubs():
    assert _count("今天天气很好。我们去公园散步吧。", 5.0, sensitivity="sentence")["pieces"] == 2
    # "好。" would be a 1-character stub
    assert _count("好。我们现在就去公园散步吧。", 5.0, sensitivity="sentence")["split_lines"] == 0
    # 0.8 s floor: the second sentence would get under 0.8 s
    assert _count("今天天气非常非常好我们去公园散步吧。去吧去吧。", 3.0,
                  sensitivity="sentence")["split_lines"] == 0


def test_min_piece_rule_in_english():
    r = _count("We should leave now. Go. Then we can eat together.", 12.0, sensitivity="sentence")
    assert r["pieces"] == 2  # the one-word "Go." folds into a neighbour


def test_no_cut_inside_quotes_abbreviations_or_numbers():
    en = "Dr. Smith paid 3.5 dollars at No. 5 Main St. yesterday. He said \"Stop. Go home now.\" Then he left."
    did, ids = _one_line(en, 30.0, source="en")
    svc.resplit_long_lines(did, ids, sensitivity="sentence")
    assert [r["zh"] for r in db.load_lines(did)] == [
        "Dr. Smith paid 3.5 dollars at No. 5 Main St. yesterday.",
        "He said \"Stop. Go home now.\" Then he left."]
    zh = "他说：「你好。我是小明。请多关照。」然后他就走了。再见了朋友。"
    did, ids = _one_line(zh, 12.0)
    svc.resplit_long_lines(did, ids, sensitivity="sentence")
    assert [r["zh"] for r in db.load_lines(did)] == [
        "他说：「你好。我是小明。请多关照。」", "然后他就走了。", "再见了朋友。"]


def test_duration_cap_replaces_the_default_limit():
    short = "我今天早上很早就起床了。然后去公园跑步。"
    assert _count(short, 20.0)["split_lines"] == 1  # Normal: over 8 s
    assert _count(short, 20.0, max_seconds=30)["split_lines"] == 0
    assert _count(short, 20.0, max_seconds=10)["pieces"] == 2
    # a cap alone never starts cutting English by length
    assert _count(SAMPLES["en"], 7.0, source="en", max_seconds=10)["split_lines"] == 0


def test_normal_without_options_is_todays_behaviour():
    did, ids = _seed()
    r = svc.resplit_long_lines(did, ids, sensitivity="normal", max_seconds=None)
    assert (r["split_lines"], r["line_count"]) == (1, 5)


def test_dry_run_writes_nothing_and_needs_no_confirm():
    did, ids = _seed(en="old translation")
    before = db.load_lines(did)
    r = svc.resplit_long_lines(did, ids, dry_run=True)
    assert (r["dry_run"], r["split_lines"], r["pieces"], r["line_count"]) == (True, 1, 3, 5)
    assert r["cleared_translations"] == 1
    assert db.load_lines(did) == before and db.list_line_history(did) == []
    with pytest.raises(ConflictError):
        svc.resplit_long_lines(did, [999], dry_run=True)


def test_nothing_message_names_the_sensitivity_and_suggests_more():
    did = db.create_drama(title_zh="D")
    db.save_lines(did, [Line(idx=0, start=0, end=2, zh=SHORT)])
    ids = [r["id"] for r in db.load_lines(did)]
    note = svc.resplit_long_lines(did, ids)["note"]
    assert "Normal" in note and "More" in note and "No line is over" in note
    assert "Sentence by sentence" in svc.resplit_long_lines(did, ids, sensitivity="more")["note"]
    assert "shorter duration" in svc.resplit_long_lines(did, ids, sensitivity="sentence")["note"]


def test_nothing_message_when_over_limit_but_no_cut_point():
    did, ids = _one_line("我今天早上很早就起床了然后去公园跑步路上遇到了老朋友我们聊了很久后来一起吃了早饭", 60.0)
    note = svc.resplit_long_lines(did, ids, sensitivity="sentence", max_seconds=10)["note"]
    assert note.startswith("1 line with no sentence end")


def test_line_with_no_punctuation_or_words_is_cut_evenly_and_flagged():
    text = "".join(chr(0x4e00 + i) for i in range(100))
    did, ids = _one_line(text, 97.0)
    r = svc.resplit_long_lines(did, ids)
    assert r["split_lines"] == 1 and r["line_count"] > 2
    lines = db.load_line_objects(did)
    assert "".join(l.zh for l in lines) == text
    assert max(len(l.zh) for l in lines) <= 40
    assert {l.flag for l in lines} == {"timing_uncertain"}
    assert lines[0].start == 0.0 and lines[-1].end == 97.0


def test_sensitivity_and_cap_validation():
    did, ids = _seed()
    for bad in ({"sensitivity": "extreme"}, {"max_seconds": 1}, {"max_seconds": 500}):
        with pytest.raises(InvalidInputError):
            svc.resplit_long_lines(did, ids, **bad)


def test_route_sensitivity_fields(isolated_db):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from api.api_config import ApiSettings
    from api.server import create_app
    did, ids = _seed()
    c = TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                   base_url="http://127.0.0.1:8600")
    url = f"/api/restructure/dramas/{did}/resplit"
    for bad in ({"sensitivity": "x"}, {"max_seconds": 1}, {"max_seconds": 121}, {"dry_run": "yes"},
                {"max_seconds": "ten"}):
        assert c.post(url, json={"expected_line_ids": ids, **bad}).status_code == 422, bad
    r = c.post(url, json={"expected_line_ids": ids, "sensitivity": "more", "max_seconds": 15,
                          "dry_run": True})
    assert r.status_code == 200, r.text
    assert r.json()["dry_run"] is True and r.json()["pieces"] == 6 and len(db.load_lines(did)) == 3


# --- bounded / incremental sentence-end checks ---------------------------------

def _reference_cuts(text):
    """The original whole-prefix implementation, kept to prove the bounded and
    incremental one cuts the same places on ordinary text."""
    import re
    import core

    def inside(end):
        head = text[:end]
        return (head.count('"') % 2 == 1
                or any(head.count(o) > head.count(c) for o, c in core._QUOTE_PAIRS))

    def keep(_t, m):
        if inside(m.end()):
            return False
        if not m.group().startswith("."):
            return True
        word = re.search(r"(\S+)$", text[:m.start()])
        word = word.group(1).strip("\"'“‘([").lower() if word else ""
        if (word.rstrip(".") in core._ABBREVIATIONS or word.isdigit()
                or re.fullmatch(r"(?:[a-z]\.)+[a-z]", word)):
            return False
        if len(word) == 1 and word != "i":
            return False
        after = text[m.end():m.end() + 1]
        return not (after.islower() or (after.isdigit() and word in core._NUMBERED_BEFORE_DIGIT))
    return core._cut_after(text, core._SENTENCE_END_RE, keep)


EQUIV_TEXTS = [
    SENT * 3,
    "他说：「你好。我是小明。请多关照。」然后他就走了。再见了朋友。",
    "これは長い文です。次の文もあります！「引用。です。」最後。",
    "오늘 아침에 일찍 일어났어요. 공원에서 달리기를 했어요? 정말 좋았어요.",
    "Dr. Smith paid 3.5 dollars at No. 5 Main St. yesterday. He said \"Stop. Go home now.\" Then he left.",
    "We use e.g. apples, i.e. fruit. I. M. Pei built it. It cost 3.5 million. Done. ok then.",
    "“First. Second.” Third. 『a。b。』 c。",
]


@pytest.mark.parametrize("text", EQUIV_TEXTS)
def test_incremental_sentence_cuts_match_the_whole_prefix_version(text):
    import core
    assert core._cut_after(text, core._SENTENCE_END_RE, core._sentence_keep()) == _reference_cuts(text)


def _timed(fn, limit=2.0):
    t = time.perf_counter()
    out = fn()
    assert time.perf_counter() - t < limit
    return out


def _sentence_split(text, seconds):
    import core
    rules = core.SplitRules(None, None, per_sentence=True)
    return core.split_long_segments([{"start": 0.0, "end": seconds, "text": text}], rules=rules)


@pytest.mark.parametrize("n", [100_000, 1_000_000])
def test_adversarial_dots_after_a_long_unbroken_run_stay_fast(n):
    _timed(lambda: _sentence_split("x" * n + ". . . .", 60.0))


def test_many_sentence_ends_and_quotes_stay_linear():
    _timed(lambda: _sentence_split("好。" * 100_000, 600.0))
    _timed(lambda: _sentence_split("a. B. " * 50_000, 600.0))
    _timed(lambda: _sentence_split("「好。" * 100_000 + "」" * 5, 600.0))
    _timed(lambda: _sentence_split('"a. ' * 50_000, 600.0))


def test_alternating_one_word_sentences_fold_in_one_pass():
    pieces = _timed(lambda: _sentence_split("This is a longer sentence. Go. " * 20_000, 6000.0))
    assert all(len(p["text"].split()) >= 2 for p in pieces)


def test_resplit_leaves_a_line_over_the_cap_unsplit_and_says_so():
    did = db.create_drama(title_zh="D", source_language="zh")
    db.save_lines(did, [Line(idx=0, start=0.0, end=60.0, zh="好。" * svc.RESPLIT_MAX_CHARS)])
    ids = [r["id"] for r in db.load_lines(did)]
    out = svc.resplit_long_lines(did, ids, sensitivity="sentence", dry_run=True)
    assert out["split_lines"] == 0
    assert "1 line over the limits" in out["note"]


def test_resplit_uses_the_titles_pause(monkeypatch):
    import core
    did, ids = _seed()
    db.update_drama(did, min_pause_sec=0.8)
    seen = []
    real = core.split_long_segments
    monkeypatch.setattr(core, "split_long_segments",
                        lambda segs, **kw: seen.append(kw["min_pause"]) or real(segs, **kw))
    svc.resplit_long_lines(did, ids)
    assert seen and set(seen) == {0.8}


def test_resegmentation_preview_uses_the_titles_pause(monkeypatch):
    did, _ids = _seed()
    db.update_drama(did, min_pause_sec=0.6)
    seen = []
    monkeypatch.setattr(svc.resegment, "resegment_lines",
                        lambda lines, language, **kw: seen.append(kw["min_pause"]) or (lines, []))
    svc.preview_resegmentation(did)
    assert seen == [0.6]


def test_resegment_preview_says_why_nothing_changes():
    did, _ = _one_line("你好。", 60.0)  # short text that runs 60 s: not a character problem
    p = svc.preview_resegmentation(did)
    assert p["changed"] == [] and "Split long lines" in p["reason"]
    clean, _ = _one_line("你好。", 2.0)
    assert "Nothing to re-segment" in svc.preview_resegmentation(clean)["reason"]
