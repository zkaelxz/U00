"""The source-match check flags translations far from their source and never breaks a run."""
import json

from core import Line
from engine_backends import alignment_check
from lib import http

SRC = {"我不会原谅你": [1.0, 0.0], "明天见面": [0.0, 1.0], "你好吗朋友": [1.0, 1.0]}


def _embed(texts):
    # The English text carries its own vector as "x,y" so each case sets its similarity.
    return [SRC[t] if t in SRC else [float(v) for v in t.split(",")] for t in texts]


def _line(zh, en, flag=None):
    return Line(idx=0, start=0, end=1, zh=zh, en=en, flag=flag)


def test_flags_only_lines_below_the_bar():
    right, wrong = _line("我不会原谅你", "0.9,0.1"), _line("明天见面", "1.0,0.0")
    assert alignment_check.flag_mismatches([right, wrong], _embed) == 1
    assert right.flag is None
    assert wrong.flag == alignment_check.ALIGNMENT_FLAG and "0.00" in wrong.flag_note


def test_keeps_another_flag_and_clears_its_own_once_fixed():
    other = _line("明天见面", "1.0,0.0", flag="uncertain_translation")
    stale = _line("我不会原谅你", "0.9,0.1", flag=alignment_check.ALIGNMENT_FLAG)
    alignment_check.flag_mismatches([other, stale], _embed)
    assert other.flag == "uncertain_translation"
    assert stale.flag is None and stale.flag_note == ""


def test_short_sources_and_untranslated_lines_are_skipped():
    short, blank = _line("嗯", "1.0,0.0"), _line("明天见面", "")
    calls = []
    assert alignment_check.flag_mismatches([short, blank], lambda t: calls.append(t)) == 0
    assert calls == [] and short.flag is None and blank.flag is None


def test_unavailable_embedding_leaves_lines_alone():
    ln = _line("明天见面", "1.0,0.0")
    assert alignment_check.flag_mismatches([ln], lambda t: None) == 0
    assert ln.flag is None


def test_embed_batches_and_reads_vectors(monkeypatch):
    sent = []

    def post(url, **kw):
        sent.append((url, kw["json"]))
        n = len(kw["json"]["input"])
        return http.Response(200, {}, json.dumps({"embeddings": [[1.0]] * n}).encode(), url)

    monkeypatch.setattr(alignment_check.http, "post", post)
    out = alignment_check.embed("http://o:1", ["a"] * (alignment_check.EMBED_BATCH + 1))
    assert len(out) == alignment_check.EMBED_BATCH + 1 and len(sent) == 2
    assert sent[0][0] == "http://o:1/api/embed" and sent[0][1]["model"] == "bge-m3"


def test_embed_gives_up_connecting_quickly_but_reads_slowly(monkeypatch):
    seen = {}

    def post(url, **kw):
        seen["timeout"] = kw["timeout"]
        return http.Response(200, {}, json.dumps({"embeddings": [[1.0]]}).encode(), url)

    monkeypatch.setattr(alignment_check.http, "post", post)
    alignment_check.embed("http://o:1", ["a"])
    assert seen["timeout"] == (5, alignment_check.EMBED_TIMEOUT)


def test_embed_returns_none_when_model_missing_or_server_down(monkeypatch):
    monkeypatch.setattr(alignment_check.http, "post",
                        lambda url, **kw: http.Response(404, {}, b'{"error":"x"}', url))
    assert alignment_check.embed("http://o:1", ["a"]) is None

    def down(url, **kw):
        raise http.FetchError("down")

    monkeypatch.setattr(alignment_check.http, "post", down)
    assert alignment_check.embed("http://o:1", ["a"]) is None


def test_setting_is_on_by_default_and_gates_the_embedder(isolated_db):
    from services import settings_service
    assert settings_service.get("translate_alignment_check") is True
    assert callable(settings_service.alignment_embedder())
    settings_service.set_settings({"translate_alignment_check": False})
    assert settings_service.alignment_embedder() is None


def _finish(db, embed_texts):
    from types import SimpleNamespace as NS
    import translation_finish
    did = db.create_drama(title_en="T", status="aligned")
    db.save_lines(did, [Line(idx=0, start=0, end=2, zh="我不会原谅你", en=""),
                        Line(idx=1, start=2, end=4, zh="明天见面", en="")])
    lines = db.load_line_objects(did)
    lines[0].en, lines[1].en = "0.9,0.1", "1.0,0.0"
    db.save_lines(did, lines, fields=("en",))
    translation_finish.finish_translation_run(
        did, lines, NS(model="fake"), "claude", "audio_drama", glossary_terms=[], errors=[],
        enforce_ids={ln.id for ln in lines}, embed_texts=embed_texts)
    return {r["idx"]: r for r in db.load_lines(did)}


def test_finish_run_saves_the_flag_only_when_the_check_is_given(isolated_db):
    rows = _finish(isolated_db, _embed)
    assert rows[0]["flag"] is None
    assert rows[1]["flag"] == alignment_check.ALIGNMENT_FLAG
    assert "Source match" in rows[1]["flag_note"]


def test_finish_run_without_the_check_flags_nothing(isolated_db):
    assert all(r["flag"] is None for r in _finish(isolated_db, None).values())


def test_finish_run_survives_a_broken_check(isolated_db):
    def boom(texts):
        raise RuntimeError("embedder exploded")

    rows = _finish(isolated_db, boom)
    assert all(r["flag"] is None for r in rows.values())


def test_a_text_the_server_cannot_embed_is_left_unchecked_not_fatal():
    ok, bad = _line("明天见面", "0.1,0.9"), _line("我不会原谅你", "0.9,0.1")
    unchecked = []

    def embed(texts):
        return [None if t == "我不会原谅你" else SRC[t] if t in SRC else [0.1, 0.9] for t in texts]

    assert alignment_check.flag_mismatches([ok, bad], embed, unchecked=unchecked) == 0
    assert unchecked == [bad] and bad.flag is None


def _nan_server(monkeypatch, limit=60):
    """Ollama 0.24 with bge-m3: HTTP 500 for any request holding a text of `limit` characters or more."""
    sent = []

    def post(url, **kw):
        texts = kw["json"]["input"]
        sent.append(list(texts))
        if any(len(t) >= limit for t in texts):
            return http.Response(500, {}, b'{"error":"json: unsupported value: NaN"}', url)
        return http.Response(200, {}, json.dumps({"embeddings": [[float(len(t)), 1.0] for t in texts]}).encode(), url)

    monkeypatch.setattr(alignment_check.http, "post", post)
    return sent


def test_a_long_text_is_embedded_in_pieces_and_averaged(monkeypatch):
    sent = _nan_server(monkeypatch)
    long_text = "这是一个很长的句子，" * 10  # 100 characters
    out = alignment_check.embed("http://o:1", ["短句", long_text])
    assert len(out) == 2 and out[0] == [2.0, 1.0]
    assert out[1] is not None and out[1][1] == 1.0
    assert all(len(t) < 60 for batch in sent[-1:] for t in batch)


def test_a_text_that_fails_even_in_pieces_gives_none_in_its_place(monkeypatch):
    _nan_server(monkeypatch, limit=1)
    assert alignment_check.embed("http://o:1", ["短句", "又一句话"]) == [None, None]


def test_pieces_cut_at_break_marks_and_stay_short():
    text = "一二三四五，" * 20
    pieces = alignment_check._pieces(text)
    assert all(len(p) <= alignment_check.PIECE_CHARS for p in pieces)
    assert "".join(pieces).replace("，", "") == text.replace("，", "")


def _shifted(own):
    """Three lines whose middle English carries the vector `own`; each source is a unit axis."""
    zh = {"前一句话": [1.0, 0.0, 0.0], "中间一句话": [0.0, 1.0, 0.0], "后一句话": [0.0, 0.0, 1.0]}
    lines = [_line("前一句话", "1.0,0.0,0.0"), _line("中间一句话", own), _line("后一句话", "0.0,0.0,1.0")]

    def embed(texts):
        return [zh[t] if t in zh else [float(v) for v in t.split(",")] for t in texts]

    return lines, embed


def test_english_nearer_the_next_lines_source_is_flagged_even_above_the_bar():
    # Scores 0.6 against its own source, above the bar, but 0.8 against the next line's.
    lines, embed = _shifted("0.0,0.6,0.8")
    assert alignment_check.flag_mismatches(lines, embed) == 1
    assert lines[1].flag == alignment_check.ALIGNMENT_FLAG and "next line" in lines[1].flag_note
    assert lines[0].flag is None and lines[2].flag is None


def test_english_clearly_nearest_its_own_source_is_not_flagged_for_a_neighbour():
    lines, embed = _shifted("0.0,0.9,0.5")
    assert alignment_check.flag_mismatches(lines, embed) == 0 and lines[1].flag is None


def test_the_neighbour_test_can_be_turned_off():
    lines, embed = _shifted("0.0,0.6,0.8")
    assert alignment_check.flag_mismatches(lines, embed, neighbour_margin=None) == 0


def test_a_line_between_which_and_its_neighbour_sits_a_skipped_line_is_not_compared():
    lines, embed = _shifted("0.0,0.6,0.8")
    skipped = _line("嗯", "x")  # too short to check, so it is not in the checked list
    assert alignment_check.flag_mismatches([lines[0], lines[1], skipped, lines[2]], embed) == 0


# --- the judge: a model confirms the lines the embeddings doubt ---

def _judged(own, verdicts):
    lines, embed = _shifted(own)
    asked = []

    def judge(items):
        asked.append(items)
        return {it["id"]: verdicts.get(it["id"]) for it in items if it["id"] in verdicts}

    return lines, embed, judge, asked


def test_a_judge_clears_a_doubt_it_finds_ok():
    # 0.03 closer to the next line is a doubt at the looser margin but not a flag at the strict one.
    lines, embed, judge, asked = _judged("0.0,0.9,0.95", {1: "ok"})
    assert alignment_check.flag_mismatches(lines, embed, judge=judge) == 0
    assert [it["id"] for it in asked[0]] == [1]
    assert lines[1].flag is None


def test_a_judge_confirms_a_shift_and_the_note_says_a_check_found_it():
    lines, embed, judge, _ = _judged("0.0,0.6,0.8", {1: "shifted"})
    assert alignment_check.flag_mismatches(lines, embed, judge=judge) == 1
    assert "a check found" in lines[1].flag_note and "neighbouring line" in lines[1].flag_note


def test_a_judge_that_overrules_a_strict_flag_leaves_the_line_unflagged():
    lines, embed, judge, _ = _judged("0.0,0.6,0.8", {1: "ok"})
    assert alignment_check.flag_mismatches(lines, embed, judge=judge) == 0


def test_a_line_the_judge_gives_no_verdict_for_falls_back_to_the_embedding_scores():
    lines, embed, judge, _ = _judged("0.0,0.6,0.8", {})
    assert alignment_check.flag_mismatches(lines, embed, judge=judge) == 1
    lines, embed, judge, _ = _judged("0.0,0.9,0.95", {})
    assert alignment_check.flag_mismatches(lines, embed, judge=judge) == 0


def test_the_judge_sees_the_neighbours_and_is_not_asked_about_clear_lines():
    lines, embed, judge, asked = _judged("0.0,0.9,0.1", {})
    assert alignment_check.flag_mismatches(lines, embed, judge=judge) == 0
    assert asked == []
    lines, embed, judge, asked = _judged("0.0,0.6,0.8", {1: "ok"})
    alignment_check.flag_mismatches(lines, embed, judge=judge)
    [item] = asked[0]
    assert item["prev"] == ("前一句话", "1.0,0.0,0.0") and item["next"] == ("后一句话", "0.0,0.0,1.0")


def test_a_judge_saying_ok_cannot_clear_a_line_scoring_under_the_bar():
    lines, embed, judge, _ = _judged("0.6,0.1,0.0", {1: "ok"})
    assert alignment_check.flag_mismatches(lines, embed, judge=judge) == 1


def test_embedding_stops_between_requests_when_the_run_is_stopped(monkeypatch):
    calls = []
    monkeypatch.setattr(alignment_check, "_post_embed", lambda *a: calls.append(1) or (200, [[1.0]] * 32))
    assert alignment_check.embed("http://o:1", ["x"] * 64, should_stop=lambda: len(calls) >= 1) is None
    assert len(calls) == 1


def test_embedding_reports_progress_before_each_request(monkeypatch):
    seen = []
    monkeypatch.setattr(alignment_check, "_post_embed", lambda *a: (200, [[1.0]] * len(a[2])))
    alignment_check.embed("http://o:1", ["x"] * 40, on_progress=lambda done, total: seen.append((done, total)))
    assert seen == [(0, 40), (32, 40)]
