"""The optional personal VTuber name list: parsing, download, status, matching,
the per-title opt-in, the prompt block, the suggestions and the routes."""

import json
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
from lib import http
from services import auth_service, glossary_variants_service, vdb_names_service as vn
from lib.errors import DependencyUnavailableError

REMOTE = "https://baihe.example.com"


def _entry(default="", cn="", jp="", en="", extra=(), type="vtuber", **kw):
    return {"uuid": "u", "type": type, "accounts": [], "name": {"default": default, "cn": cn, "jp": jp, "en": en,
                                                               "extra": list(extra)}, **kw}


FIXTURE = {
    "meta": {"UUID_NAMESPACE": "x", "linkSyntax": {}, "timestamp": 1700000000000},
    "vtbs": [
        _entry(default="白上フブキ", jp="白上フブキ", en="Shirakami Fubuki", extra=["フブキ", "x"]),
        _entry(cn="星街彗星", en="Hoshimachi Suisei"),
        _entry(default="Mel", type="group"),
        _entry(default="某粉丝", type="fan"),
        _entry(default="杂项", type="misc"),
        _entry(default="机器人名", bot=True),
        "not an entry",
        {"type": "vtuber", "name": "not a dict"},
        {"type": "vtuber"},
        _entry(default="  Pekora  ", extra=[None, 5, "Pekora", "a" * 41, "line\nbreak", "a,b"]),
    ],
}


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(vn.portable, "data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(vn, "_cache", None)
    return tmp_path


def _fake_get(monkeypatch, body, status=200, calls=None):
    def get(url, **kw):
        if calls is not None:
            calls.append((url, kw))
        return http.Response(status=status, headers={}, body=body if isinstance(body, bytes) else json.dumps(body).encode(),
                             url=url)
    monkeypatch.setattr(vn.http, "get", get)


def _install(data_dir, monkeypatch, names):
    _fake_get(monkeypatch, {"vtbs": [_entry(default=n) for n in names]})
    return vn.fetch()


def test_parse_keeps_vtubers_and_groups_with_bounded_names():
    kept, names = vn.parse_list(FIXTURE)
    assert kept == 4
    assert "白上フブキ" in names and "フブキ" in names and "Shirakami Fubuki" in names
    assert "Pekora" in names and "Mel" in names
    for dropped in ("x", "a,b", "某粉丝", "杂项", "机器人名", "a" * 41, "line\nbreak", "  Pekora  "):
        assert dropped not in names
    assert names == sorted(set(names))


def test_parse_refuses_a_document_without_a_list():
    for bad in ([], {}, {"vtbs": "x"}, None):
        with pytest.raises(DependencyUnavailableError):
            vn.parse_list(bad)


def test_fetch_uses_the_fixed_address_a_timeout_and_a_cap(data_dir, monkeypatch):
    calls = []
    _fake_get(monkeypatch, FIXTURE, calls=calls)
    status = vn.fetch()
    (url, kw), = calls
    assert url == "https://vdb.vtbs.moe/json/list.json"
    assert kw["timeout"] and kw["max_bytes"] <= 8 * 1024 * 1024 and kw["deadline"]
    assert status["downloaded"] and status["entry_count"] == 4 and status["name_count"] > 4
    stored = json.loads((data_dir / vn.FILE_NAME).read_text(encoding="utf-8"))
    assert stored["header"]["source"] == "dd-center/vdb" and "CC BY-NC-SA" in stored["header"]["licence"]
    assert stored["header"]["fetched_at"]


def test_oversized_download_is_refused_and_keeps_the_old_list(data_dir, monkeypatch):
    _install(data_dir, monkeypatch, ["旧名字"])

    def too_big(url, **kw):
        raise http.ResponseTooLarge()
    monkeypatch.setattr(vn.http, "get", too_big)
    with pytest.raises(DependencyUnavailableError):
        vn.fetch()
    assert vn.entries_for_text("旧名字") == ["旧名字"]
    assert os.listdir(data_dir) == [vn.FILE_NAME]


@pytest.mark.parametrize("status,body", [(500, b"oops"), (200, b"not json"), (200, b'{"vtbs": 3}'),
                                           (200, b'{"vtbs": []}')])
def test_a_bad_answer_writes_nothing(data_dir, monkeypatch, status, body):
    _fake_get(monkeypatch, body, status=status)
    with pytest.raises(DependencyUnavailableError):
        vn.fetch()
    assert os.listdir(data_dir) == []


def test_write_is_atomic_when_replacing_fails(data_dir, monkeypatch):
    _install(data_dir, monkeypatch, ["旧名字"])

    def boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(vn.os, "replace", boom)
    _fake_get(monkeypatch, {"vtbs": [_entry(default="新名字")]})
    with pytest.raises(DependencyUnavailableError):
        vn.fetch()
    assert os.listdir(data_dir) == [vn.FILE_NAME]
    assert vn.entries_for_text("旧名字") == ["旧名字"]


def test_status_has_no_path_or_url_and_delete_removes_the_file(data_dir, monkeypatch):
    assert vn.status() == {"downloaded": False, "entry_count": 0, "name_count": 0,
                           "age_days": None, "newer_than_30_days": False}
    st = _install(data_dir, monkeypatch, ["白上フブキ"])
    assert st["downloaded"] and st["age_days"] == 0 and st["newer_than_30_days"]
    assert all(isinstance(v, (bool, int, type(None))) for v in st.values())
    assert str(data_dir) not in json.dumps(st) and "vtbs" not in json.dumps(st)
    assert vn.delete()["downloaded"] is False
    assert not (data_dir / vn.FILE_NAME).exists()
    assert vn.delete()["downloaded"] is False


def test_an_old_download_is_not_newer_than_30_days(data_dir, monkeypatch):
    _install(data_dir, monkeypatch, ["白上フブキ"])
    path = data_dir / vn.FILE_NAME
    data = json.loads(path.read_text(encoding="utf-8"))
    data["header"]["fetched_at"] = "2020-01-01T00:00:00+00:00"
    path.write_text(json.dumps(data), encoding="utf-8")
    st = vn.status()
    assert st["downloaded"] and st["age_days"] > 30 and st["newer_than_30_days"] is False


def test_a_damaged_file_reads_as_not_downloaded(data_dir):
    (data_dir / vn.FILE_NAME).write_text("{broken", encoding="utf-8")
    assert vn.status()["downloaded"] is False and vn.entries_for_text("anything") == []


def test_matching_is_case_sensitive_and_needs_latin_boundaries(data_dir, monkeypatch):
    _install(data_dir, monkeypatch, ["Mel", "Pekora", "白上フブキ", "星街"])
    assert vn.entries_for_text("Mel sang") == ["Mel"]
    assert vn.entries_for_text("Melon and melody") == []
    assert vn.entries_for_text("mel and PEKORA") == []
    assert vn.entries_for_text("Hi, Pekora! (Mel)") == ["Mel", "Pekora"] or set(vn.entries_for_text("Hi, Pekora! (Mel)")) == {"Mel", "Pekora"}
    assert vn.entries_for_text("今天白上フブキ来了") == ["白上フブキ"]
    assert vn.entries_for_text("Pekoraa") == []
    assert vn.matches_for_text("Mel, Mel and Mel") == [("Mel", 3)]


def test_matching_is_bounded(data_dir, monkeypatch):
    names = [f"名字{i:04d}" for i in range(300)]
    _install(data_dir, monkeypatch, names)
    assert len(vn.entries_for_text("".join(names))) == vn.MAX_MATCHES
    assert vn.matches_for_text("名字0001" * 5000)[0][1] == vn.MAX_COUNT_PER_NAME


def test_every_file_the_service_writes_is_git_ignored():
    # A non-installed copy keeps its data folder in the git checkout, and the list is
    # licence-restricted, so it must never be committable by mistake.
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in (vn.FILE_NAME, vn.FILE_NAME + vn.TEMP_SUFFIX):
        r = subprocess.run(["git", "check-ignore", "-q", name], cwd=root)
        assert r.returncode == 0, f"{name} is not in .gitignore"


@pytest.mark.parametrize("entry", [{"type": ["vtuber"], "name": {"default": "名字名字"}},
                                   {"type": {"a": 1}, "name": {"default": "名字名字"}}])
def test_a_list_or_dict_type_is_skipped_not_fatal(entry):
    assert vn.parse_list({"vtbs": [entry, _entry(default="好名字")]}) == (1, ["好名字"])


def test_a_hostile_download_is_a_fixed_error_not_a_crash(data_dir, monkeypatch):
    for body in (b"[" * 200000 + b"]" * 200000, b'{"vtbs": [{"type": ["x"]}], "meta": []}' + b"\x00"):
        _fake_get(monkeypatch, body)
        with pytest.raises(DependencyUnavailableError, match="expected format"):
            vn.fetch()
    assert os.listdir(data_dir) == []


def test_a_nested_stored_file_reads_as_not_downloaded(data_dir):
    (data_dir / vn.FILE_NAME).write_text("[" * 200000 + "]" * 200000, encoding="utf-8")
    assert vn.status()["downloaded"] is False and vn.entries_for_text("名字名字") == []


def test_a_failed_write_is_a_fixed_error_without_a_path(data_dir, monkeypatch):
    _fake_get(monkeypatch, FIXTURE)
    monkeypatch.setattr(vn.os, "makedirs", lambda *a, **k: (_ for _ in ()).throw(OSError(str(data_dir))))
    with pytest.raises(DependencyUnavailableError) as exc:
        vn.fetch()
    assert str(data_dir) not in exc.value.message and "could not be saved" in exc.value.message


# --- per title ----------------------------------------------------------------

def _drama(**kw):
    return db.create_drama(title_en="T", series_id=db.get_or_create_series("S"), source_language="zh", **kw)


def _lines(*texts):
    return [Line(idx=i, start=0, end=1, zh=t) for i, t in enumerate(texts)]


def test_titles_are_off_by_default_and_the_prompt_is_unchanged(isolated_db, data_dir, monkeypatch):
    _install(data_dir, monkeypatch, ["白上フブキ"])
    did = _drama()
    drama = db.get_drama(did)
    assert vn.get_title(did) == {"enabled": False, "downloaded": True}
    assert vn.block_for(drama, _lines("白上フブキ来了")) == ""
    vn.set_title(did, True)
    assert vn.block_for(drama, _lines("白上フブキ来了")) == "\n\nKnown names (keep as written): 白上フブキ"
    vn.set_title(did, False)
    assert vn.block_for(drama, _lines("白上フブキ来了")) == ""


def test_prompt_block_is_stable_capped_and_never_overrides_a_term(isolated_db, data_dir, monkeypatch):
    names = [f"名字{i:03d}" for i in range(60)]
    _install(data_dir, monkeypatch, names + ["白上フブキ"])
    did = _drama()
    vn.set_title(did, True)
    drama = db.get_drama(did)
    lines = _lines(*names, "白上フブキ")
    block = vn.block_for(drama, lines)
    assert block == vn.block_for(drama, lines)
    assert len(block.split(": ", 1)[1].split(", ")) == vn.MAX_PROMPT_NAMES
    covered = vn.block_for(drama, lines, [{"term_original": "白上フブキ", "aliases": []}])
    assert "白上フブキ" not in covered
    assert vn.block_for(drama, _lines("nothing here")) == ""
    assert vn.block_for(None, lines) == ""


def test_nothing_is_added_without_a_download(isolated_db, data_dir):
    did = _drama()
    vn.set_title(did, True)
    assert vn.block_for(db.get_drama(did), _lines("白上フブキ")) == ""


def test_deleting_a_title_drops_its_choice(isolated_db, data_dir):
    a, b = _drama(), _drama()
    vn.set_title(a, True)
    vn.set_title(b, True)
    from services import drama_service
    drama_service.delete_drama(a, confirm=True, confirm_text="DELETE")
    assert db.get_app_setting(f"vdb_names.title.{a}") is None
    assert vn.is_enabled(b)


def test_possible_names_offers_known_names_only_when_opted_in(isolated_db, data_dir, monkeypatch):
    _install(data_dir, monkeypatch, ["白上フブキ", "星街彗星"])
    did = _drama()
    db.save_lines(did, _lines("白上フブキ你好", "白上フブキ再见"))
    assert glossary_variants_service.discover_names(did)["known_vtuber_names"] == []
    vn.set_title(did, True)
    (card,) = glossary_variants_service.discover_names(did)["known_vtuber_names"]
    assert card["suggested_original"] == "白上フブキ" and card["total"] == 2
    assert card["existing_term"] is None and card["spellings"][0]["sources"] == []
    from services import glossary_service
    glossary_service.upsert_glossary_term(did, {"term_original": "白上フブキ", "term_translation": "Fubuki"})
    (card,) = glossary_variants_service.discover_names(did)["known_vtuber_names"]
    assert card["existing_term"] == "白上フブキ" and card["spellings"][0]["is_term"] is True


# --- routes -------------------------------------------------------------------

def _app(auth="on"):
    return create_app(ApiSettings(auth_mode=auth, serve_frontend=False))


def _login(email):
    u = auth_service.add_user(email)
    for p in auth_service.OPT_IN_PERMISSIONS:
        auth_service.grant_permission(u["id"], p)
    s = auth_service.create_session(u["id"])
    return u["id"], {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}", api_auth.CSRF_HEADER: s["csrf_token"]}


def test_route_permissions():
    decls = {(m, p): d for _r, p, methods, d in api_auth.iter_route_declarations(_app())
             for m in methods if p.startswith("/api/vdb-names")}
    assert decls[("POST", "/api/vdb-names/fetch")] == [("local_only", None)]
    assert decls[("DELETE", "/api/vdb-names")] == [("local_only", None)]
    assert decls[("GET", "/api/vdb-names")] == [("permission", "library.read")]
    assert decls[("POST", "/api/vdb-names/dramas/{drama_id}")] == [("permission", "lines.edit")]


def test_download_and_delete_are_refused_away_from_the_pc(isolated_db, data_dir, monkeypatch):
    _fake_get(monkeypatch, FIXTURE)
    _, headers = _login("a@example.com")
    c = TestClient(_app(), base_url=REMOTE, raise_server_exceptions=False)
    assert c.post("/api/vdb-names/fetch", json={}, headers=headers).status_code == 403
    assert c.delete("/api/vdb-names", headers=headers).status_code == 403
    assert not (data_dir / vn.FILE_NAME).exists()


def test_local_download_status_and_delete_round_trip(isolated_db, data_dir, monkeypatch):
    _fake_get(monkeypatch, FIXTURE)
    c = TestClient(_app(auth="off"), raise_server_exceptions=False)
    r = c.post("/api/vdb-names/fetch", json={})
    assert r.status_code == 200 and r.json()["downloaded"] is True
    assert str(data_dir) not in r.text and "vdb.vtbs.moe" not in r.text
    assert c.get("/api/vdb-names").json()["name_count"] > 0
    assert c.delete("/api/vdb-names").json()["downloaded"] is False


def test_a_failed_download_answers_without_url_or_path(isolated_db, data_dir, monkeypatch):
    _fake_get(monkeypatch, b"x", status=503)
    c = TestClient(_app(auth="off"), raise_server_exceptions=False)
    r = c.post("/api/vdb-names/fetch", json={})
    assert r.status_code >= 400 and "vtbs.moe" not in r.text and str(data_dir) not in r.text


def test_title_routes_check_ownership(isolated_db, data_dir):
    a_id, a = _login("a@example.com")
    b_id, b = _login("b@example.com")
    private = db.create_drama(title_en="P", source_language="zh", owner_user_id=a_id, is_private=1)
    shared = db.create_drama(title_en="S", source_language="zh", owner_user_id=a_id, is_private=0)
    c = TestClient(_app(), base_url=REMOTE, raise_server_exceptions=False)
    url = f"/api/vdb-names/dramas/{private}"
    assert c.get(url, headers=b).status_code == 404
    assert c.post(url, json={"enabled": True}, headers=b).status_code == 404
    assert c.get(url, headers=a).json() == {"enabled": False, "downloaded": False}
    assert c.post(url, json={"enabled": True}, headers=a).json()["enabled"] is True
    # A shared title is editable by the household, as for language packs.
    assert c.post(f"/api/vdb-names/dramas/{shared}", json={"enabled": True}, headers=b).status_code == 200
    assert c.get(f"/api/vdb-names/dramas/{shared}", headers=b).status_code == 200
