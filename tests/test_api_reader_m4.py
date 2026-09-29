"""
Route batch 2B (M4): the Reader API over services/reader_service.py.

Fully mocked: no network, no models, no real LLM. Engines are faked by
patching translate_service.resolve_api_key / translate_engines.get_engine
and the story/wiki/QA helpers the service calls.
"""
import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from services import auth_service, reader_service

BASE = "/api/reader/dramas"
REMOTE = "https://baihe.example.com"


def _local(app):
    return TestClient(app, base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                      raise_server_exceptions=False)


@pytest.fixture
def client(isolated_db):
    return _local(create_app(ApiSettings()))


def _lines(n, zh="你好", en="Hello"):
    return [Line(idx=i, start=float(i), end=float(i + 1), zh=f"{zh}{i}",
                 en=f"{en} {i}" if en else "") for i in range(n)]


@pytest.fixture
def drama(isolated_db):
    did = db.create_drama(title_en="Story", source_language="zh")
    db.save_lines(did, _lines(10))
    return did


class _FakeEngine:
    supports_reference = True
    model = "fake"


@pytest.fixture
def fake_engine(monkeypatch):
    from services import translate_service
    import translate_engines
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, env_path=None: "k")
    monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: _FakeEngine())


def _code(r):
    return r.json()["error"]["code"]


# Every route with a body/query that passes validation, so an unknown drama
# is the only reason to fail.
ROUTES = [
    ("GET", "/page", None),
    ("GET", "/overview", None),
    ("POST", "/progress", {"page": 1}),
    ("GET", "/notes", None),
    ("POST", "/notes", {"notes": "x"}),
    ("GET", "/media", None),
    ("GET", "/captions/Source", None),
    ("GET", "/captions/English/readout", None),
    ("POST", "/lookup", {"page": 1}),
    ("GET", "/vocab", None),
    ("POST", "/vocab/rich", {"words": ["猫"]}),
    ("GET", "/vocab/export.csv", None),
    ("GET", "/vocab/export.apkg", None),
    ("POST", "/story/who", {"name": "A"}),
    ("POST", "/story/explain", {"phrase": "A"}),
    ("POST", "/story/recap", {"page": 1}),
    ("POST", "/story/relationships", {}),
    ("GET", "/wiki", None),
    ("POST", "/wiki/update", {}),
    ("POST", "/wiki/clear", {"confirm": True}),
    ("GET", "/wiki/export.md", None),
    ("POST", "/ask", {"question": "Who?"}),
]
LLM_ROUTES = [(p, b) for m, p, b in ROUTES
              if p.startswith("/story/") or p in ("/wiki/update", "/ask")]


def _call(c, method, path, body, **kw):
    if method == "GET":
        return c.get(path, **kw)
    return c.post(path, json=body, **kw)


def test_every_reader_route_is_covered():
    app = create_app(ApiSettings())
    paths = {(sorted(m)[0], p[len(BASE) + len("/{drama_id}"):])
             for _r, p, m, _d in api_auth.iter_route_declarations(app) if p.startswith(BASE)}
    listed = {(m, p.replace("/Source", "/{track}").replace("/English", "/{track}"))
              for m, p, _b in ROUTES}
    assert paths == listed


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_unknown_drama_is_404(client, method, path, body, fake_engine):
    r = _call(client, method, f"{BASE}/99999{path}", body)
    assert r.status_code == 404 and _code(r) == "not_found"


# --- reads, progress, notes ------------------------------------------------

def test_overview_progress_and_notes(client, drama):
    r = client.get(f"{BASE}/{drama}/overview")
    assert r.status_code == 200
    assert r.json()["line_count"] == 10 and r.json()["last_page"] == 1
    r = client.post(f"{BASE}/{drama}/progress", json={"page": 1, "chapter_size": 10})
    assert r.status_code == 200 and r.json()["last_line_idx"] == 9
    assert client.get(f"{BASE}/{drama}/overview").json()["last_line_idx"] == 9
    # past the last page, chapter_size out of range, unknown field
    assert client.post(f"{BASE}/{drama}/progress", json={"page": 2, "chapter_size": 10}).status_code == 422
    assert client.post(f"{BASE}/{drama}/progress", json={"page": 1, "chapter_size": 5}).status_code == 422
    assert client.post(f"{BASE}/{drama}/progress", json={"page": 1, "x": 1}).status_code == 422

    assert client.post(f"{BASE}/{drama}/notes", json={"notes": "mine"}).json()["notes"] == "mine"
    assert client.get(f"{BASE}/{drama}/notes").json() == {"drama_id": drama, "notes": "mine"}
    assert client.post(f"{BASE}/{drama}/notes",
                       json={"notes": "x" * 100_001}).status_code == 422


def test_progress_on_drama_without_lines_is_404(client, isolated_db):
    did = db.create_drama(title_en="Empty", source_language="zh")
    assert client.post(f"{BASE}/{did}/progress", json={"page": 1}).status_code == 404


# --- captions and media ------------------------------------------------------

def test_caption_track_is_vtt_and_absent_track_404(client, isolated_db):
    did = db.create_drama(title_en="Src only", source_language="zh")
    db.save_lines(did, _lines(3, en=""))
    r = client.get(f"{BASE}/{did}/captions/Source")
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/vtt; charset=utf-8"
    assert r.text.startswith("WEBVTT") and "你好0" in r.text
    assert "content-disposition" not in r.headers
    assert client.get(f"{BASE}/{did}/captions/English").status_code == 404
    assert client.get(f"{BASE}/{did}/captions/Bilingual").status_code == 404
    assert client.get(f"{BASE}/{did}/captions/French").status_code == 422


def test_readout(client, drama):
    r = client.get(f"{BASE}/{drama}/captions/English/readout")
    assert r.status_code == 200
    body = r.json()
    assert body["track"] == "English" and len(body["lines"]) == 10
    assert body["lines"][0]["text"] == "Hello 0" and body["lines"][0]["timestamp"] == "00:00"


def test_media_availability_no_paths(client, drama):
    folder = db.drama_dir(drama)
    with open(os.path.join(folder, "source.mp4"), "wb") as f:
        f.write(b"x")
    with open(os.path.join(folder, "dub_track.wav"), "wb") as f:
        f.write(b"x")
    db.update_drama(drama, source_video_filename="source.mp4")
    r = client.get(f"{BASE}/{drama}/media")
    assert r.status_code == 200
    assert r.json() == {"drama_id": drama, "original": "video", "dub": True, "narration": False,
                        "caption_tracks": ["Source", "English", "Bilingual"],
                        "captions_overlay": True}
    assert "source.mp4" not in r.text and folder not in r.text


# --- lookup and vocab ----------------------------------------------------------

@pytest.fixture
def lookup_drama(isolated_db, monkeypatch):
    import dictionary
    import segment
    monkeypatch.setattr(segment, "segment_and_annotate",
                        lambda text, language, chinese_script="simplified":
                        [(w, "") for w in text.split(" ")])
    monkeypatch.setattr(dictionary, "lookup_cedict",
                        lambda w: {"word": w, "pinyin": "māo", "definitions": ["cat"]}
                        if w == "猫" else None)
    did = db.create_drama(title_en="L", source_language="zh")
    db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="猫 狗 鸟", en="")])
    return did


def test_lookup_without_llm_makes_no_llm_call(client, lookup_drama, monkeypatch):
    import translate_engines
    monkeypatch.setattr(translate_engines, "call_llm_json",
                        lambda *a, **k: pytest.fail("no LLM call without use_llm"))
    monkeypatch.setattr(translate_engines, "get_engine",
                        lambda *a, **k: pytest.fail("no engine without use_llm"))
    r = client.post(f"{BASE}/{lookup_drama}/lookup", json={"page": 1, "use_llm": False})
    assert r.status_code == 200
    assert r.json()["definitions"] == {"猫": {"reading": "māo", "definitions": ["cat"]}}
    assert r.json()["saved"] == 1
    assert [w["word"] for w in client.get(f"{BASE}/{lookup_drama}/vocab").json()["words"]] == ["猫"]


def test_lookup_with_llm_matches_by_id(client, lookup_drama, fake_engine, monkeypatch):
    import translate_engines
    monkeypatch.setattr(translate_engines, "call_llm_json", lambda *a, **k:
                        '{"2": {"reading": "niǎo", "definitions": ["bird"]}}')
    r = client.post(f"{BASE}/{lookup_drama}/lookup",
                    json={"page": 1, "use_llm": True, "engine": "ollama"})
    assert r.status_code == 200
    defs = r.json()["definitions"]
    assert defs["鸟"] == {"reading": "niǎo", "definitions": ["bird"]} and "狗" not in defs


def test_lookup_validation(client, lookup_drama):
    assert client.post(f"{BASE}/{lookup_drama}/lookup", json={"page": 0}).status_code == 422
    assert client.post(f"{BASE}/{lookup_drama}/lookup",
                       json={"page": 1, "model": "m" * 101}).status_code == 422
    assert client.post(f"{BASE}/{lookup_drama}/lookup",
                       json={"page": 1, "use_llm": "yes"}).status_code == 422


def _vocab_drama(title_en=None, title_zh=None):
    did = db.create_drama(title_en=title_en, title_zh=title_zh, source_language="zh")
    db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="我有一只猫", en="I have a cat")])
    db.save_vocab_lookup(did, "猫", "māo", ["cat"], "zh", 0)
    db.save_vocab_lookup(did, "有", "yǒu", ["to have"], "zh", 0)
    return did


def test_vocab_list_and_rich_queue(client, isolated_db):
    did = _vocab_drama(title_en="V")
    r = client.post(f"{BASE}/{did}/vocab/rich", json={"words": ["猫"]})
    assert r.status_code == 200 and r.json()["rich_count"] == 1
    body = client.get(f"{BASE}/{did}/vocab", params={"rich_only": True}).json()
    assert [w["word"] for w in body["words"]] == ["猫"] and body["words"][0]["export_rich"] is True
    assert client.get(f"{BASE}/{did}/vocab").json()["count"] == 2
    assert client.post(f"{BASE}/{did}/vocab/rich", json={"words": []}).status_code == 422
    assert client.post(f"{BASE}/{did}/vocab/rich",
                       json={"words": ["x"] * 501}).status_code == 422


def test_rich_queue_cross_drama_is_404(client, isolated_db):
    a = _vocab_drama(title_en="A")
    b = db.create_drama(title_en="B", source_language="zh")
    db.save_vocab_lookup(b, "狗", "gǒu", ["dog"], "zh", 0)
    r = client.post(f"{BASE}/{a}/vocab/rich", json={"words": ["狗"]})
    assert r.status_code == 404
    assert not db.list_vocab_lookups(b, rich_only=True)


def test_vocab_csv_chinese_title_gets_ascii_filename(client, isolated_db):
    did = _vocab_drama(title_zh="白鹤之恋")
    r = client.get(f"{BASE}/{did}/vocab/export.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    cd = r.headers["content-disposition"]
    assert cd == f'attachment; filename="drama_{did}_vocab.csv"'
    cd.encode("ascii")
    assert "猫 (māo)" in r.text


def test_vocab_csv_empty_is_404(client, drama):
    assert client.get(f"{BASE}/{drama}/vocab/export.csv").status_code == 404


def test_apkg_without_genanki_is_503(client, isolated_db, monkeypatch):
    did = _vocab_drama(title_zh="白鹤")
    import vocab_export

    def missing(*a, **k):
        raise ImportError("No module named genanki at /home/secret/site-packages")
    monkeypatch.setattr(vocab_export, "export_vocab_apkg", missing)
    r = client.get(f"{BASE}/{did}/vocab/export.apkg")
    assert r.status_code == 503 and _code(r) == "dependency_unavailable"
    assert "/home/secret" not in r.text


def test_apkg_with_genanki(client, isolated_db):
    pytest.importorskip("genanki")
    did = _vocab_drama(title_zh="白鹤")
    r = client.get(f"{BASE}/{did}/vocab/export.apkg")
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert r.headers["content-disposition"] == f'attachment; filename="drama_{did}_vocab.apkg"'
    assert client.get(f"{BASE}/{did}/vocab/export.apkg",
                      params={"rich": True}).status_code == 404   # nothing queued yet
    client.post(f"{BASE}/{did}/vocab/rich", json={"words": ["猫"]})
    r = client.get(f"{BASE}/{did}/vocab/export.apkg", params={"rich": True})
    assert r.status_code == 200
    assert r.headers["content-disposition"] == \
        f'attachment; filename="drama_{did}_vocab_sentence.apkg"'


# --- story tools, wiki, Q&A --------------------------------------------------

def test_story_tools(client, drama, fake_engine, monkeypatch):
    import story_context
    seen = {}
    monkeypatch.setattr(story_context, "who_is_character",
                        lambda name, lines, meta, eng: seen.setdefault("n", len(lines)) and "A hero")
    r = client.post(f"{BASE}/{drama}/story/who", json={"name": "沈清疑", "up_to_line_idx": 3})
    assert r.status_code == 200 and r.json() == {"drama_id": drama, "answer": "A hero"}
    assert seen["n"] == 4
    monkeypatch.setattr(story_context, "explain_reference",
                        lambda p, lines, eng, source_language="zh": f"{p}/{len(lines)}")
    assert client.post(f"{BASE}/{drama}/story/explain",
                       json={"phrase": "一石二鸟"}).json()["answer"] == "一石二鸟/10"
    monkeypatch.setattr(story_context, "summarize_section",
                        lambda lines, eng, section_label="": f"{len(lines)} {section_label}")
    assert client.post(f"{BASE}/{drama}/story/recap",
                       json={"page": 1, "chapter_size": 10}).json()["summary"] == "10 up to page 1"
    monkeypatch.setattr(story_context, "build_relationship_map", lambda lines, eng: {
        "characters": [{"name": "A", "role": "lead", "description": "d"}], "relationships": []})
    r = client.post(f"{BASE}/{drama}/story/relationships", json={})
    assert r.status_code == 200 and r.json()["characters"][0]["name"] == "A"
    assert "A" in r.json()["mermaid"]
    # validation
    assert client.post(f"{BASE}/{drama}/story/who", json={"name": ""}).status_code == 422
    assert client.post(f"{BASE}/{drama}/story/who", json={"name": "  "}).status_code == 422
    assert client.post(f"{BASE}/{drama}/story/who",
                       json={"name": "A", "up_to_line_idx": -1}).status_code == 422


def test_engine_error_is_redacted(client, drama, fake_engine, monkeypatch):
    import story_context
    secret = "sk-ant-api03-" + "A" * 40

    def boom(*a, **k):
        raise RuntimeError(f"bad key {secret} at /home/user/secret/file.py")
    monkeypatch.setattr(story_context, "who_is_character", boom)
    r = client.post(f"{BASE}/{drama}/story/who", json={"name": "A"})
    assert r.status_code == 500
    assert secret not in r.text and "/home/user/secret" not in r.text


def test_no_key_is_503(client, drama, monkeypatch):
    from services import translate_service
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, env_path=None: None)
    r = client.post(f"{BASE}/{drama}/ask", json={"question": "Q?", "engine": "claude"})
    assert r.status_code == 503


def test_wiki_update_list_export_clear(client, drama, fake_engine, monkeypatch):
    import universe_wiki

    def fake_extract(lines, eng, up_to, meta, existing_entries=None):
        return [{"entry_type": "character", "name": "Early", "description": "e",
                 "first_seen_line_idx": 1, "known_through_line_idx": up_to},
                {"entry_type": "place", "name": "Late", "first_seen_line_idx": 8,
                 "known_through_line_idx": up_to}]
    monkeypatch.setattr(universe_wiki, "extract_wiki_entries", fake_extract)
    r = client.post(f"{BASE}/{drama}/wiki/update", json={"up_to_line_idx": 9})
    assert r.status_code == 200 and r.json()["updated"] == 2
    shown = client.get(f"{BASE}/{drama}/wiki", params={"up_to_line_idx": 3}).json()["entries"]
    assert [e["name"] for e in shown] == ["Early"]
    assert client.get(f"{BASE}/{drama}/wiki", params={"entry_type": "spaceship"}).status_code == 422
    r = client.get(f"{BASE}/{drama}/wiki/export.md", params={"up_to_line_idx": 3})
    assert r.status_code == 200 and "Early" in r.text and "Late" not in r.text
    assert r.headers["content-type"].startswith("text/markdown")
    assert r.headers["content-disposition"] == \
        f'attachment; filename="drama_{drama}_universe_wiki.md"'

    # clear: no confirm -> 422 and entries kept
    for body in ({}, {"confirm": False}, {"confirm": "true"}):
        r = client.post(f"{BASE}/{drama}/wiki/clear", json=body)
        assert r.status_code == 422
        assert len(client.get(f"{BASE}/{drama}/wiki").json()["entries"]) == 2
    r = client.post(f"{BASE}/{drama}/wiki/clear", json={"confirm": True})
    assert r.status_code == 200 and r.json() == {"drama_id": drama, "cleared": True}
    assert client.get(f"{BASE}/{drama}/wiki").json()["entries"] == []


def test_ask_passes_history_and_validates(client, drama, fake_engine, monkeypatch):
    import qa
    got = {}

    def fake(q, lines, meta, eng, chat_history=None):
        got.update(q=q, h=chat_history)
        return "answer"
    monkeypatch.setattr(qa, "ask_about_drama", fake)
    hist = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
    r = client.post(f"{BASE}/{drama}/ask", json={"question": "Who?", "chat_history": hist})
    assert r.status_code == 200 and r.json()["answer"] == "answer"
    assert got == {"q": "Who?", "h": hist}
    bad = [
        {"question": "x" * 2001},
        {"question": "Q", "chat_history": [{"role": "system", "content": "x"}]},
        {"question": "Q", "chat_history": [{"role": "user", "content": "x", "extra": 1}]},
        {"question": "Q", "chat_history": [{"role": "user", "content": "x"}] * 41},
        {"question": "Q", "chat_history": [{"role": "user", "content": "x" * 20_000}] * 6},
    ]
    for body in bad:
        assert client.post(f"{BASE}/{drama}/ask", json=body).status_code == 422


# --- no paths or secrets anywhere ------------------------------------------------

def test_no_path_in_any_body_or_header(client, isolated_db, fake_engine, monkeypatch):
    did = _vocab_drama(title_en="P")
    folder = db.drama_dir(did)
    with open(os.path.join(folder, "audio.wav"), "wb") as f:
        f.write(b"x")
    db.update_drama(did, audio_filename="audio.wav")
    db.upsert_wiki_entry(did, "character", "A", description="d", first_seen_line_idx=0)
    import qa
    import story_context
    import universe_wiki
    monkeypatch.setattr(story_context, "who_is_character", lambda *a, **k: "x")
    monkeypatch.setattr(story_context, "explain_reference", lambda *a, **k: "x")
    monkeypatch.setattr(story_context, "summarize_section", lambda *a, **k: "x")
    monkeypatch.setattr(story_context, "build_relationship_map", lambda *a, **k: {})
    monkeypatch.setattr(universe_wiki, "extract_wiki_entries", lambda *a, **k: [])
    monkeypatch.setattr(qa, "ask_about_drama", lambda *a, **k: "x")
    import dictionary
    import segment
    monkeypatch.setattr(segment, "segment_and_annotate", lambda text, language, chinese_script="": [])
    monkeypatch.setattr(dictionary, "lookup_cedict", lambda w: None)
    monkeypatch.setattr(reader_service.reader, "build_reader_html", lambda *a, **k: "<html></html>")
    needles = [folder, os.path.realpath(folder), db.LIBRARY_DIR, "audio.wav"]
    for method, path, body in ROUTES:
        if path == "/vocab/export.apkg":
            continue   # optional dependency; covered above
        if path == "/wiki/clear":
            continue   # would empty the data the other routes read
        r = _call(client, method, f"{BASE}/{did}{path}", body)
        assert r.status_code == 200, (path, r.status_code, r.text)
        blob = (r.text if not path.endswith(".apkg") else "") + str(dict(r.headers))
        for n in needles:
            assert n not in blob, (path, n)


# --- auth on: household user, CSRF ----------------------------------------------

def _remote(app):
    return TestClient(app, base_url=REMOTE, raise_server_exceptions=False)


def _session(email, perms=None):
    u = auth_service.add_user(email)
    if perms is not None:  # replace the household defaults with exactly these
        for p in auth_service.HOUSEHOLD_DEFAULT_PERMISSIONS:
            if p not in perms:
                auth_service.revoke_permission(u["id"], p)
        for p in perms:
            if p not in auth_service.HOUSEHOLD_DEFAULT_PERMISSIONS:
                auth_service.grant_permission(u["id"], p)
    return auth_service.create_session(u["id"])


def _h(session, csrf=True):
    h = {"Cookie": f"{api_auth.COOKIE_NAME}={session['session_token']}"}
    if csrf:
        h[api_auth.CSRF_HEADER] = session["csrf_token"]
    return h


@pytest.fixture
def on_client(isolated_db):
    return _remote(create_app(ApiSettings(auth_mode="on")))


def test_auth_on_401_without_session(on_client):
    for method, path, body in ROUTES:
        r = _call(on_client, method, f"{BASE}/1{path}", body)
        assert r.status_code == 401, path


@pytest.mark.parametrize("path,body", LLM_ROUTES)
def test_household_llm_routes_free_engines_only(on_client, path, body):
    s = _session("kid@example.com")
    url = f"{BASE}/99999{path}"
    assert on_client.post(url, json=body, headers=_h(s)).status_code == 403          # omitted
    assert on_client.post(url, json={**body, "engine": "claude"},
                          headers=_h(s)).status_code == 403
    r = on_client.post(url, json={**body, "engine": "ollama"}, headers=_h(s))
    assert r.status_code == 404   # past auth; unknown drama


@pytest.mark.parametrize("path,body", LLM_ROUTES)
def test_engines_paid_allows_claude(on_client, path, body):
    s = _session("paid@example.com", perms=list(auth_service.HOUSEHOLD_DEFAULT_PERMISSIONS)
                 + ["engines.paid"])
    r = on_client.post(f"{BASE}/99999{path}", json={**body, "engine": "claude"}, headers=_h(s))
    assert r.status_code == 404
    r = on_client.post(f"{BASE}/99999{path}", json=body, headers=_h(s))
    assert r.status_code == 404


def test_household_lookup(on_client):
    s = _session("kid@example.com")
    url = f"{BASE}/99999/lookup"
    assert on_client.post(url, json={"page": 1}, headers=_h(s)).status_code == 404
    assert on_client.post(url, json={"page": 1, "use_llm": True},
                          headers=_h(s)).status_code == 403
    assert on_client.post(url, json={"page": 1, "use_llm": True, "engine": "claude"},
                          headers=_h(s)).status_code == 403
    assert on_client.post(url, json={"page": 1, "use_llm": True, "engine": "ollama"},
                          headers=_h(s)).status_code == 404


def test_lookup_llm_needs_jobs_start(on_client):
    s = _session("editor@example.com", perms=["library.read", "lines.read", "lines.edit",
                                              "engines.paid"])
    url = f"{BASE}/99999/lookup"
    assert on_client.post(url, json={"page": 1}, headers=_h(s)).status_code == 404
    assert on_client.post(url, json={"page": 1, "use_llm": True, "engine": "ollama"},
                          headers=_h(s)).status_code == 403


def test_permissions_per_route(on_client, drama):
    reader_only = _session("reader@example.com", perms=["library.read"])
    lines_read = _session("lr@example.com", perms=["library.read", "lines.read"])
    # library.read reads
    for path in ("/overview", "/notes", "/media", "/vocab", "/wiki"):
        assert on_client.get(f"{BASE}/{drama}{path}", headers=_h(reader_only)).status_code == 200
    # lines.read: captions, readout, exports
    for path in ("/captions/Source", "/captions/Source/readout", "/wiki/export.md"):
        assert on_client.get(f"{BASE}/{drama}{path}", headers=_h(reader_only)).status_code == 403
        assert on_client.get(f"{BASE}/{drama}{path}", headers=_h(lines_read)).status_code == 200
    for path in ("/vocab/export.csv", "/vocab/export.apkg"):
        assert on_client.get(f"{BASE}/{drama}{path}", headers=_h(reader_only)).status_code == 403
    # lines.edit writes
    for path, body in (("/progress", {"page": 1}), ("/notes", {"notes": "n"}),
                       ("/vocab/rich", {"words": ["x"]}), ("/wiki/clear", {"confirm": True}),
                       ("/lookup", {"page": 1})):
        assert on_client.post(f"{BASE}/{drama}{path}", json=body,
                              headers=_h(lines_read)).status_code == 403
    household = _session("kid@example.com")
    assert on_client.post(f"{BASE}/{drama}/notes", json={"notes": "n"},
                          headers=_h(household)).status_code == 200
    # jobs.start for LLM routes, even with engines.paid
    no_jobs = _session("nojobs@example.com", perms=["library.read", "lines.read", "lines.edit",
                                                    "engines.paid"])
    for path, body in LLM_ROUTES:
        assert on_client.post(f"{BASE}/{drama}{path}", json={**body, "engine": "ollama"},
                              headers=_h(no_jobs)).status_code == 403


@pytest.mark.parametrize("path,body", [(p, b) for m, p, b in ROUTES if m == "POST"])
def test_posts_need_csrf(on_client, path, body):
    s = _session("kid@example.com")
    url = f"{BASE}/99999{path}"
    payload = {**body, "engine": "ollama"} if (path, body) in LLM_ROUTES else body
    assert on_client.post(url, json=payload, headers=_h(s, csrf=False)).status_code == 403
    bad = {**_h(s, csrf=False), api_auth.CSRF_HEADER: "nope"}
    assert on_client.post(url, json=payload, headers=bad).status_code == 403
    assert on_client.post(url, json=payload, headers=_h(s)).status_code == 404
