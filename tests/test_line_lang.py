"""
Line.lang -- a line's own spoken language (None = the title's
source_language): the column migration, save_lines, undo snapshots,
split/merge/re-split, the line-edit and set-language API routes, and
core.normalize_line_lang. Fully mocked: isolated_db, no network.
"""
import contextlib
import json
import sqlite3

import pytest

import core
import db
from core import Line
from services import lines_service, restructure_service
from services.service_errors import InvalidInputError, NotFoundError


def _seed(langs=(None, "ko", None), speakers=("A", "B", "A")):
    did = db.create_drama(title_zh="D", source_language="ja")
    db.save_lines(did, [Line(idx=i, start=float(i), end=i + 1.0, zh=f"z{i}", en=f"e{i}",
                             speaker=s, lang=lang)
                        for i, (lang, s) in enumerate(zip(langs, speakers))])
    return did, [r["id"] for r in db.load_lines(did)]


def _langs(did):
    return [r["lang"] for r in db.load_lines(did)]


class TestNormalize:
    @pytest.mark.parametrize("value,out", [(None, None), ("", None), ("  ", None),
                                           ("ko", "ko"), ("KO", "ko"), (" Ja ", "ja"),
                                           ("en", "en"), ("zh", "zh")])
    def test_accepts(self, value, out):
        assert core.normalize_line_lang(value) == out

    @pytest.mark.parametrize("value", ["fr", "zh-hans", "korean", 1, True, ["ko"]])
    def test_rejects_unknown(self, value):
        with pytest.raises(InvalidInputError):
            core.normalize_line_lang(value)

    def test_unknown_stored_code_reads_as_title_default(self):
        ln = core.line_from_row({"idx": 0, "start": 0, "end": 1, "zh": "x", "lang": "xx", "id": 1})
        assert ln.lang is None and ln.orig["lang"] is None
        assert core.line_from_row({"idx": 0, "start": 0, "end": 1, "zh": "x", "lang": "KO"}).lang == "ko"


class TestMigration:
    def test_old_db_gains_nullable_lang_and_keeps_lines(self, isolated_db):
        did, ids = _seed(langs=(None, None, None))
        with contextlib.closing(sqlite3.connect(db.DB_PATH)) as c:
            c.execute("ALTER TABLE lines DROP COLUMN lang")
            c.commit()
            assert "lang" not in {r[1] for r in c.execute("PRAGMA table_info(lines)")}
        db.init_db()
        with contextlib.closing(sqlite3.connect(db.DB_PATH)) as c:
            col = {r[1]: r for r in c.execute("PRAGMA table_info(lines)")}["lang"]
        assert col[2] == "TEXT" and col[3] == 0 and col[4] is None  # nullable, no default
        assert _langs(did) == [None, None, None]
        assert [ln.lang for ln in db.load_line_objects(did)] == [None, None, None]


class TestSaveLines:
    def test_full_sync_round_trip_and_orig(self, isolated_db):
        did, ids = _seed()
        lines = db.load_line_objects(did)
        assert [ln.lang for ln in lines] == [None, "ko", None]
        assert lines[1].orig["lang"] == "ko"
        lines[0].lang = "en"
        db.save_lines(did, lines)
        assert _langs(did) == ["en", "ko", None]
        assert lines[0].orig["lang"] == "en"

    def test_blank_lang_is_stored_as_null(self, isolated_db):
        did, ids = _seed()
        lines = db.load_line_objects(did)
        lines[1].lang = ""
        db.save_lines(did, lines, fields=("lang",))
        assert _langs(did) == [None, None, None]

    def test_field_scoped_writes_only_lang_and_never_inserts(self, isolated_db):
        did, ids = _seed()
        lines = db.load_line_objects(did)
        lines[0].lang, lines[0].en = "ko", "CHANGED"
        lines.append(Line(idx=3, start=3.0, end=4.0, zh="new", lang="en"))
        db.save_lines(did, lines, fields=("lang",))
        rows = db.load_lines(did)
        assert len(rows) == 3 and [r["lang"] for r in rows] == ["ko", "ko", None]
        assert rows[0]["en"] == "e0"

    def test_unchanged_lang_does_not_clobber_another_writer(self, isolated_db):
        did, ids = _seed()
        stale = db.load_line_objects(did)
        fresh = db.load_line_objects(did)
        fresh[0].lang = "en"
        db.save_lines(did, fresh, fields=("lang",))
        stale[0].en = "edited"
        db.save_lines(did, stale)  # full sync from a list loaded before the lang change
        row = db.load_lines(did)[0]
        assert row["lang"] == "en" and row["en"] == "edited"

    def test_only_if_unchanged_keeps_a_newer_lang(self, isolated_db):
        did, ids = _seed()
        mine = db.load_line_objects(did)
        other = db.load_line_objects(did)
        other[1].lang = "en"
        db.save_lines(did, other, fields=("lang",))
        mine[1].lang = "zh"
        assert db.save_lines(did, mine, fields=("lang",), only_if_unchanged=True) == {ids[1]}
        assert _langs(did)[1] == "en"

    def test_compare_and_set_on_lang(self, isolated_db):
        did, ids = _seed()
        assert not db.update_line_fields_if(did, ids[1], {"lang": "en"}, {"lang": "ja"})
        assert db.update_line_fields_if(did, ids[1], {"lang": "en"}, {"lang": "ko"})
        assert db.update_line_fields_if(did, ids[0], {"lang": "zh"}, {"lang": ""})
        assert _langs(did) == ["zh", "en", None]


class TestUndo:
    def test_snapshot_records_lang_and_restore_brings_it_back(self, isolated_db):
        did, ids = _seed()
        after = restructure_service.merge_lines(did, [ids[0], ids[1]], ids)["line_ids"]
        assert _langs(did) == [None, None]  # A(None)+B(ko) differ -> title default
        snap = db.get_line_history_snapshot(db.list_line_history(did)[0]["id"])
        assert [r["lang"] for r in snap] == [None, "ko", None]
        restructure_service.restore_version(did, db.list_line_history(did)[0]["id"], after)
        assert _langs(did) == [None, "ko", None]

    def test_old_snapshot_without_lang_keeps_current_lang(self, isolated_db):
        did, ids = _seed()
        snap = [{k: r[k] for k in ("id", "idx", "start", "end", "zh", "en", "speaker")}
                for r in db.load_lines(did)]
        with contextlib.closing(db.get_conn()) as conn:
            conn.execute("INSERT INTO line_history (drama_id, label, snapshot_json, created_at) "
                         "VALUES (?, 'old', ?, 'x')", (did, json.dumps(snap)))
            conn.commit()
        restructure_service.restore_version(did, db.list_line_history(did)[0]["id"], ids)
        assert _langs(did) == [None, "ko", None]

    def test_translation_version_round_trip(self, isolated_db):
        did, ids = _seed()
        lines = db.load_line_objects(did)
        db.save_translation_version(did, lines, "v1")
        rows = db.get_translation_version(db.list_translation_versions(did)[0]["id"])["lines"]
        assert [r["lang"] for r in rows] == [None, "ko", None]
        restored = core.restore_saved_lines(rows, db.load_line_objects(did))
        assert [ln.lang for ln in restored] == [None, "ko", None]


class TestSplitMerge:
    def test_split_keeps_lang_on_both_halves(self, isolated_db):
        did, ids = _seed()
        db.update_line_fields_if(did, ids[1], {"zh": "abcd"}, {})
        restructure_service.split_line(did, ids[1], ids, at_char=2, expected_zh="abcd")
        assert _langs(did) == [None, "ko", "ko", None]

    def test_merge_keeps_shared_lang(self, isolated_db):
        did, ids = _seed(langs=("ko", "ko", None))
        restructure_service.merge_lines(did, [ids[0], ids[1]], ids)
        assert _langs(did) == ["ko", None]

    def test_merge_of_mixed_langs_falls_back_and_flags_nothing(self, isolated_db):
        did, ids = _seed(langs=("ko", "en", None))
        restructure_service.merge_lines(did, [ids[0], ids[1]], ids)
        rows = db.load_lines(did)
        assert [r["lang"] for r in rows] == [None, None] and rows[0]["flag"] is None

    def test_merge_adjacent_short_lines_follows_the_same_rule(self):
        same = [Line(idx=i, start=i * 0.5, end=i * 0.5 + 0.4, zh="a", lang="ko") for i in range(2)]
        assert [ln.lang for ln in core.merge_adjacent_short_lines(same)] == ["ko"]
        mixed = [Line(idx=0, start=0, end=0.4, zh="a", lang="ko"),
                 Line(idx=1, start=0.5, end=0.9, zh="b", lang="ja")]
        assert [ln.lang for ln in core.merge_adjacent_short_lines(mixed)] == [None]

    def test_resegment_pieces_keep_lang(self):
        import resegment
        ln = Line(idx=0, start=0.0, end=4.0, zh="甲。乙。", lang="ko")
        new, changed = resegment.resegment_lines([ln], "zh", max_chars=2,
                                                 boundaries_fn=lambda *a: None)
        assert len(new) == 2 and [p.lang for p in new] == ["ko", "ko"]


class TestService:
    def test_patch_sets_and_clears_lang_only(self, isolated_db):
        did, ids = _seed()
        out = lines_service.patch_line(did, ids[0], lang="EN")
        assert out["lang"] == "en" and _langs(did)[0] == "en"
        out = lines_service.patch_line(did, ids[0], lang="")
        assert out["lang"] is None and db.load_lines(did)[0]["en"] == "e0"

    def test_patch_rejects_unknown_lang(self, isolated_db):
        did, ids = _seed()
        with pytest.raises(InvalidInputError):
            lines_service.patch_line(did, ids[0], lang="fr")
        assert _langs(did) == [None, "ko", None]

    def test_bulk_by_ids_skips_unknown_ids(self, isolated_db):
        did, ids = _seed()
        other, other_ids = _seed()
        out = lines_service.set_lines_lang(did, "en", line_ids=[ids[0], ids[1], other_ids[0], 999999])
        assert out == {"updated": 2, "line_ids": [ids[0], ids[1]],
                       "skipped_ids": [other_ids[0], 999999]}
        assert _langs(did) == ["en", "en", None]
        assert _langs(other) == [None, "ko", None]

    def test_bulk_by_speaker_and_clear(self, isolated_db):
        did, ids = _seed()
        out = lines_service.set_lines_lang(did, "ko", speaker="A")
        assert out["updated"] == 2 and out["line_ids"] == [ids[0], ids[2]]
        assert _langs(did) == ["ko", "ko", "ko"]
        assert lines_service.set_lines_lang(did, None, speaker="A")["updated"] == 2
        assert _langs(did) == [None, "ko", None]

    @pytest.mark.parametrize("kw", [{}, {"line_ids": [1], "speaker": "A"}, {"line_ids": []},
                                    {"line_ids": [True]}, {"line_ids": "1"}, {"speaker": " "}])
    def test_bulk_needs_exactly_one_selector(self, isolated_db, kw):
        did, ids = _seed()
        with pytest.raises(InvalidInputError):
            lines_service.set_lines_lang(did, "ko", **kw)

    def test_bulk_unknown_drama_404(self, isolated_db):
        with pytest.raises(NotFoundError):
            lines_service.set_lines_lang(999, "ko", speaker="A")


# ---- API ---------------------------------------------------------------------

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from api import auth as api_auth  # noqa: E402
from api.api_config import ApiSettings  # noqa: E402
from api.server import create_app  # noqa: E402
from services import auth_service  # noqa: E402

B = "/api/lines/dramas"


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                      headers={"X-Baihe-Local": "1"})


class TestApi:
    def test_lines_page_carries_lang(self, client):
        did, ids = _seed()
        r = client.get(f"/api/review/dramas/{did}/lines")
        assert r.status_code == 200, r.text
        assert [ln["lang"] for ln in r.json()["lines"]] == [None, "ko", None]

    def test_patch_lang(self, client):
        did, ids = _seed()
        r = client.post(f"{B}/{did}/lines/{ids[0]}", json={"lang": "ja"})
        assert r.status_code == 200 and r.json()["lang"] == "ja"
        r = client.post(f"{B}/{did}/lines/{ids[0]}", json={"lang": ""})
        assert r.status_code == 200 and r.json()["lang"] is None
        r = client.post(f"{B}/{did}/lines/{ids[0]}", json={"lang": "xx"})
        assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"

    def test_set_language_route(self, client):
        did, ids = _seed()
        r = client.post(f"{B}/{did}/set-language", json={"lang": "en", "line_ids": [ids[0]]})
        assert r.status_code == 200, r.text
        assert r.json() == {"updated": 1, "line_ids": [ids[0]], "skipped_ids": []}
        r = client.post(f"{B}/{did}/set-language", json={"lang": None, "speaker": "B"})
        assert r.status_code == 200 and _langs(did) == ["en", None, None]
        for body in ({"lang": "ko"}, {"line_ids": [ids[0]]}, {"lang": "fr", "speaker": "A"},
                     {"lang": "ko", "speaker": "A", "extra": 1}):
            assert client.post(f"{B}/{did}/set-language", json=body).status_code == 422, body
        assert client.post(f"{B}/99999/set-language",
                           json={"lang": "ko", "speaker": "A"}).status_code == 404


def _login(email):
    u = auth_service.add_user(email)
    s = auth_service.create_session(u["id"])
    return u["id"], {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
                     api_auth.CSRF_HEADER: s["csrf_token"]}


class TestOwnership:
    @pytest.mark.parametrize("route", ["set-language", "lines"])
    def test_other_users_private_drama_is_404_and_untouched(self, isolated_db, route):
        a_id, a = _login("a@example.com")
        _b_id, b = _login("b@example.com")
        did, ids = _seed()
        db.update_drama(did, owner_user_id=a_id, is_private=1)
        app = create_app(ApiSettings(auth_mode="on", serve_frontend=False))
        c = TestClient(app, base_url="https://baihe.example.com", raise_server_exceptions=False)
        if route == "set-language":
            path, body = f"{B}/{did}/set-language", {"lang": "en", "line_ids": ids}
        else:
            path, body = f"{B}/{did}/lines/{ids[0]}", {"lang": "en"}
        assert c.post(path, json=body, headers=b).status_code == 404
        assert _langs(did) == [None, "ko", None]
        assert c.post(path, json=body, headers=a).status_code == 200
        assert _langs(did)[0] == "en"


def test_resplit_pieces_keep_lang(isolated_db):
    sent = "我今天早上很早就起床了然后去公园跑步。"
    did = db.create_drama(title_zh="D", source_language="zh")
    db.save_lines(did, [Line(idx=0, start=0.0, end=30.0, zh=sent * 3, speaker="B", lang="ko")])
    ids = [r["id"] for r in db.load_lines(did)]
    assert restructure_service.resplit_long_lines(did, ids)["split_lines"] == 1
    assert _langs(did) == ["ko", "ko", "ko"]


def test_patch_expected_lang_is_compare_and_set(isolated_db):
    from services.service_errors import ConflictError
    did, ids = _seed()
    with pytest.raises(ConflictError):
        lines_service.patch_line(did, ids[1], lang="en", expected={"lang": "ja"})
    assert lines_service.patch_line(did, ids[1], lang="en", expected={"lang": "ko"})["lang"] == "en"
    assert lines_service.patch_line(did, ids[0], lang="zh", expected={"lang": ""})["lang"] == "zh"
