"""Live translation with a chosen title's glossary and language packs."""
import pytest

from jobs import job_launch
import db
import live_cue_translation as lct
import translate_engines as te
from core import Line
from engine_backends.prompts import build_batch_user_message, build_stable_system_text
from services import language_pack_service as packs, live_service
from services.service_errors import NotFoundError

OWNER = {"user_id": None, "is_admin": True, "is_local_owner": True}


class Engine:
    name = "fake"

    def __init__(self):
        self.contexts = []

    def translate_batch(self, lines, context):
        self.contexts.append(context)
        return [f"EN:{t}" for t in lines]


def _title(lang="ja", private_owner=None):
    sid = db.get_or_create_series("S")
    did = db.create_drama(title_en="T", series_id=sid, status="aligned", source_language=lang)
    db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="x")])
    return did, sid


def _hints(did, lang="ja", principal=OWNER):
    return live_service._title_term_hints(principal, did, lang)


def _prompt(engine, translator, texts):
    translator.translate_chunk(texts)
    ctx = engine.contexts[-1]
    return build_stable_system_text(ctx), build_batch_user_message(ctx, "1. " + texts[0])


def test_a_chunk_with_a_glossary_term_sends_it_and_one_without_does_not(isolated_db):
    did, sid = _title()
    db.upsert_glossary_term(sid, "真白", "Mashiro", notes="the heroine")
    engine = Engine()
    tr = lct.CueTranslator(engine, "ja", term_hints=_hints(did))
    _, with_term = _prompt(engine, tr, ["真白が笑った"])
    _, without = _prompt(engine, tr, ["今日は晴れ"])
    assert "真白 -> Mashiro (the heroine)" in with_term
    assert "Mashiro" not in without


def test_a_pack_entry_appears_only_when_the_pack_is_on(isolated_db):
    did, _ = _title()
    assert _hints(did)(["先輩、こんにちは"]) == []
    packs.set_title_packs(did, {"packs": {"ja-address": None}})
    found = _hints(did)(["先輩、こんにちは"])
    assert [h[0] for h in found] == ["先輩"] and "senpai" in found[0][1]
    assert _hints(did)(["おはよう"]) == []


def test_a_glossary_term_beats_a_pack_entry(isolated_db):
    did, sid = _title()
    packs.set_title_packs(did, {"packs": {"ja-address": None}})
    db.upsert_glossary_term(sid, "先輩", "Senior Li")
    found = _hints(did)(["先輩"])
    assert [(h[0], h[1]) for h in found] == [("先輩", "Senior Li")]


def test_the_stable_prompt_is_identical_with_and_without_a_title(isolated_db):
    did, sid = _title()
    db.upsert_glossary_term(sid, "真白", "Mashiro")
    plain, titled = Engine(), Engine()
    a = lct.CueTranslator(plain, "ja")
    b = lct.CueTranslator(titled, "ja", term_hints=_hints(did))
    stable_a, user_a = _prompt(plain, a, ["今日は晴れ"])
    stable_b, user_b = _prompt(titled, b, ["今日は晴れ"])
    stable_c, _ = _prompt(titled, b, ["真白が笑った"])
    assert stable_a == stable_b == stable_c
    assert user_a == user_b
    assert "term_hints" not in plain.contexts[0]


def test_prompt_growth_is_bounded(isolated_db):
    did, sid = _title()
    for i in range(200):
        db.upsert_glossary_term(sid, f"名{i}", f"Name{i}", notes="n" * 500)
    text = "".join(f"名{i}" for i in range(200))
    engine = Engine()
    tr = lct.CueTranslator(engine, "ja", term_hints=_hints(did))
    _, user = _prompt(engine, tr, [text])
    base = Engine()
    _, plain = _prompt(base, lct.CueTranslator(base, "ja"), [text])
    assert len(engine.contexts[0]["term_hints"]) <= 60
    assert len(user) - len(plain) < 60 * 220


def test_packs_are_skipped_when_the_stream_language_differs(isolated_db):
    did, _ = _title("ja")
    packs.set_title_packs(did, {"packs": {"ja-address": None}})
    assert _hints(did, lang="zh")(["先輩"]) == []


def test_a_failing_lookup_does_not_fail_the_cue(isolated_db):
    def boom(texts):
        raise RuntimeError("db gone")
    engine = Engine()
    assert lct.CueTranslator(engine, "ja", term_hints=boom).translate("やあ") == "EN:やあ"


def test_unknown_and_invisible_titles_are_refused(isolated_db):
    with pytest.raises(NotFoundError):
        _hints(99999)
    uid = db.auth_create_user("a@example.com")
    other = db.auth_create_user("b@example.com")
    did, _ = _title()
    member = {"user_id": other, "is_admin": False, "is_local_owner": False, "admin_override": False}
    db.update_drama(did, owner_user_id=uid, is_private=1)
    with pytest.raises(NotFoundError):
        _hints(did, principal=member)


def test_start_session_passes_hints_and_refuses_before_building_an_engine(isolated_db, monkeypatch):
    seen = {}
    built = []
    monkeypatch.setattr(live_service, "_require_public", lambda *a, **k: None)
    monkeypatch.setattr(live_service, "_build_engine", lambda e, m: built.append(1) or ("ollama", Engine()))
    monkeypatch.setattr(job_launch, "start_job", lambda *a, **kw: seen.update(kw) or True)
    with pytest.raises(NotFoundError):
        live_service.start_session("https://example.com/s", glossary_drama_id=424242, principal=OWNER)
    assert built == []
    live_service.start_session("https://example.com/s", principal=OWNER)
    assert seen["term_hints"] is None
    did, _ = _title()
    live_service.start_session("https://example.com/s", glossary_drama_id=did, principal=OWNER)
    assert callable(seen["term_hints"])
    live_service._sessions.clear()


def test_nothing_is_written_to_the_title(isolated_db, monkeypatch):
    did, sid = _title()
    packs.set_title_packs(did, {"packs": {"ja-address": None}})
    db.upsert_glossary_term(sid, "真白", "Mashiro")
    writers = [n for n in dir(db) if n.startswith(("save_", "update_", "upsert_", "delete_", "set_",
                                                   "create_", "add_", "remove_"))]
    calls = []
    for name in writers:
        monkeypatch.setattr(db, name, lambda *a, _n=name, **k: calls.append(_n))
    engine = Engine()
    tr = lct.CueTranslator(engine, "ja", term_hints=_hints(did))
    tr.translate_chunk(["真白と先輩"])
    tr.translate("真白")
    assert calls == []


def _series_hints(sid, lang="ja", principal=OWNER):
    return live_service._series_term_hints(principal, sid, lang)


def test_a_series_pick_uses_the_series_glossary_and_the_newest_titles_packs(isolated_db):
    sid = db.get_or_create_series("S")
    old = db.create_drama(title_en="Old", series_id=sid, source_language="ja")
    newest_with_choice = db.create_drama(title_en="New", series_id=sid, source_language="ja")
    db.create_drama(title_en="Newer", series_id=sid, source_language="ja")
    db.upsert_glossary_term(sid, "真白", "Mashiro")
    packs.set_title_packs(old, {"packs": {}})
    packs.set_title_packs(newest_with_choice, {"packs": {"ja-address": None}})
    found = _series_hints(sid)(["真白、先輩"])
    assert [h[0] for h in found] == ["真白", "先輩"]
    # The newest title has no pack choice of its own, so it is skipped rather than ending the search.


def test_a_series_with_no_pack_choice_gives_glossary_only(isolated_db):
    sid = db.get_or_create_series("S")
    db.create_drama(title_en="T", series_id=sid, source_language="ja")
    db.upsert_glossary_term(sid, "真白", "Mashiro")
    db.set_app_setting("language_packs.default.ja", {"packs": {"ja-address": None}})
    found = _series_hints(sid)(["真白、先輩"])
    assert [h[0] for h in found] == ["真白"]


def test_a_title_pick_is_unchanged_by_the_series_path(isolated_db):
    did, sid = _title()
    db.upsert_glossary_term(sid, "真白", "Mashiro")
    packs.set_title_packs(did, {"packs": {"ja-address": None}})
    assert [h[0] for h in _hints(did)(["真白、先輩"])] == ["真白", "先輩"]


def test_a_private_series_of_another_user_is_refused(isolated_db):
    owner = db.auth_create_user("a@example.com")
    other = db.auth_create_user("b@example.com")
    sid = db.get_or_create_series("S", owner_user_id=owner, is_private=True)
    member = {"user_id": other, "is_admin": False, "is_local_owner": False, "admin_override": False}
    with pytest.raises(NotFoundError):
        _series_hints(sid, principal=member)
    with pytest.raises(NotFoundError):
        _series_hints(99999)


def test_start_session_takes_a_series_and_refuses_both(isolated_db, monkeypatch):
    seen = {}
    monkeypatch.setattr(live_service, "_require_public", lambda *a, **k: None)
    monkeypatch.setattr(live_service, "_build_engine", lambda e, m: ("ollama", Engine()))
    monkeypatch.setattr(job_launch, "start_job", lambda *a, **kw: seen.update(kw) or True)
    did, sid = _title()
    with pytest.raises(live_service.InvalidInputError):
        live_service.start_session("https://example.com/s", glossary_drama_id=did, series_id=sid,
                                   principal=OWNER)
    with pytest.raises(NotFoundError):
        live_service.start_session("https://example.com/s", series_id=424242, principal=OWNER)
    live_service.start_session("https://example.com/s", series_id=sid, principal=OWNER)
    assert callable(seen["term_hints"])
    live_service._sessions.clear()
