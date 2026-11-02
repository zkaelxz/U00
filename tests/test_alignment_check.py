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


def test_embed_returns_none_when_model_missing_or_server_down(monkeypatch):
    monkeypatch.setattr(alignment_check.http, "post",
                        lambda url, **kw: http.Response(404, {}, b'{"error":"x"}', url))
    assert alignment_check.embed("http://o:1", ["a"]) is None

    def down(url, **kw):
        raise http.FetchError("down")

    monkeypatch.setattr(alignment_check.http, "post", down)
    assert alignment_check.embed("http://o:1", ["a"]) is None


def test_setting_is_off_by_default_and_gates_the_embedder(isolated_db):
    from services import settings_service
    assert settings_service.get("translate_alignment_check") is False
    assert settings_service.alignment_embedder() is None
    settings_service.set_settings({"translate_alignment_check": True})
    assert callable(settings_service.alignment_embedder())


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
