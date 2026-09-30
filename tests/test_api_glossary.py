"""
Tests for the /api/glossary endpoints (Migration Slice 46): series
glossary terms, project/series instructions and the option catalogues.
FastAPI TestClient against an isolated library -- no network.
"""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from api.api_config import ApiSettings
from api.server import create_app


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _error(resp):
    body = resp.json()
    assert set(body) == {"error"}, body
    assert {"code", "message"} <= set(body["error"])
    return body["error"]


def _drama(db, name="S", series=True):
    sid = db.get_or_create_series(name) if series else None
    return db.create_drama(title_en=f"D-{name}", series_id=sid)


FULL = {"term_original": "沈清疑", "term_translation": "Shen Qingyi", "notes": "hero",
        "category": "person_name", "policy": "keep_pinyin", "enforce_exact": True,
        "aliases": ["清疑", "Qing Yi"], "banned_translations": ["Shen Clear Doubt"]}


def _url(did, tail):
    return f"/api/glossary/dramas/{did}/{tail}"


class TestTerms:
    def test_upsert_list_round_trip(self, client, isolated_db):
        did = _drama(isolated_db)
        assert client.get(_url(did, "terms")).json() == []
        r = client.post(_url(did, "terms"), json=FULL)
        assert r.status_code == 200
        saved = r.json()
        for k, v in FULL.items():
            assert saved[k] == v
        assert client.get(_url(did, "terms")).json() == [saved]

    def test_update_by_id_keeps_omitted_fields(self, client, isolated_db):
        did = _drama(isolated_db)
        saved = client.post(_url(did, "terms"), json=FULL).json()
        r = client.post(_url(did, "terms"),
                        json={"id": saved["id"], "term_translation": "Shen Q."})
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == saved["id"]
        assert body["term_translation"] == "Shen Q."
        assert body["aliases"] == FULL["aliases"]
        assert body["banned_translations"] == FULL["banned_translations"]
        assert body["enforce_exact"] is True
        assert len(client.get(_url(did, "terms")).json()) == 1

    def test_no_series(self, client, isolated_db):
        did = _drama(isolated_db, series=False)
        assert client.get(_url(did, "terms")).json() == []
        r = client.post(_url(did, "terms"), json=FULL)
        assert r.status_code == 400
        assert _error(r)["code"] == "unsupported_operation"

    def test_cross_series_isolation(self, client, isolated_db):
        a = _drama(isolated_db, "A")
        b = _drama(isolated_db, "B")
        tid = client.post(_url(a, "terms"), json=FULL).json()["id"]
        assert client.get(_url(b, "terms")).json() == []
        r = client.post(_url(b, "terms"), json={"id": tid, "term_translation": "x"})
        assert r.status_code == 404
        r = client.post(_url(b, "terms/bulk-delete"), json={"term_ids": [tid], "confirm": True})
        assert r.json() == {"deleted": [], "not_found": [tid]}
        assert client.get(_url(a, "terms")).json()[0]["term_translation"] == "Shen Qingyi"

    def test_conflict_on_rename(self, client, isolated_db):
        did = _drama(isolated_db)
        client.post(_url(did, "terms"), json=FULL)
        other = client.post(_url(did, "terms"),
                            json={"term_original": "X", "term_translation": "x"}).json()
        r = client.post(_url(did, "terms"),
                        json={"id": other["id"], "term_original": FULL["term_original"]})
        assert r.status_code == 409
        assert _error(r)["code"] == "conflict"

    def test_validation_errors(self, client, isolated_db):
        did = _drama(isolated_db)
        r = client.post(_url(did, "terms"), json={"term_original": "a"})
        assert r.status_code == 422
        _error(r)
        r = client.post(_url(did, "terms"), json={**FULL, "unexpected": 1})
        assert r.status_code == 422
        assert _error(r)["code"] == "validation_error"
        r = client.post(_url(did, "terms"), json={**FULL, "category": "bogus"})
        assert r.status_code == 422
        r = client.post(_url(did, "terms"), json={**FULL, "aliases": ["a|b"]})
        assert r.status_code == 422
        r = client.get("/api/glossary/dramas/0/terms")
        assert r.status_code == 422

    def test_error_message_does_not_echo_user_text(self, client, isolated_db):
        did = _drama(isolated_db)
        secret = "SECRETVALUE-xyz"
        r = client.post(_url(did, "terms"), json={**FULL, "category": secret})
        assert r.status_code == 422
        assert secret not in r.text
        r = client.post(_url(did, "terms"), json={**FULL, "aliases": [secret + "|"]})
        assert r.status_code == 422
        assert secret not in r.text

    def test_unknown_drama(self, client):
        assert client.get(_url(999, "terms")).status_code == 404
        assert client.post(_url(999, "terms"), json=FULL).status_code == 404
        assert client.post(_url(999, "terms/bulk-delete"),
                           json={"term_ids": [1], "confirm": True}).status_code == 404
        assert client.get(_url(999, "instructions")).status_code == 404
        r = client.post(_url(999, "instructions/project"), json={"text": "x"})
        assert r.status_code == 404
        assert _error(r)["code"] == "not_found"


class TestInstructions:
    def test_project_and_series_independent(self, client, isolated_db):
        did = _drama(isolated_db)
        assert client.get(_url(did, "instructions")).json() == {
            "project_instructions": "", "series_instructions": ""}
        r = client.post(_url(did, "instructions/project"), json={"text": "proj"})
        assert r.json() == {"project_instructions": "proj", "series_instructions": ""}
        r = client.post(_url(did, "instructions/series"), json={"text": "ser"})
        assert r.json() == {"project_instructions": "proj", "series_instructions": "ser"}
        assert client.get(_url(did, "instructions")).json() == {
            "project_instructions": "proj", "series_instructions": "ser"}

    def test_series_instructions_without_series(self, client, isolated_db):
        did = _drama(isolated_db, series=False)
        r = client.post(_url(did, "instructions/series"), json={"text": "ser"})
        assert r.status_code == 400
        assert _error(r)["code"] == "unsupported_operation"
        r = client.post(_url(did, "instructions/project"), json={"text": "ok"})
        assert r.status_code == 200
        assert r.json()["project_instructions"] == "ok"

    def test_too_long_and_bad_body(self, client, isolated_db):
        did = _drama(isolated_db)
        r = client.post(_url(did, "instructions/project"), json={"text": "x" * 6000})
        assert r.status_code == 422
        assert "xxxxx" not in r.text
        r = client.post(_url(did, "instructions/project"), json={})
        assert r.status_code == 422
        _error(r)


class TestCatalogues:
    def test_shape(self, client):
        r = client.get("/api/glossary/catalogues")
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"style_presets", "term_categories", "term_policies",
                             "workflow_tiers"}
        assert {"key", "label"} <= set(body["style_presets"][0])
        assert any(c["key"] == "person_name" for c in body["term_categories"])
        assert {"key", "label", "example"} <= set(body["term_policies"][0])
        tier = body["workflow_tiers"][0]
        assert set(tier) == {"key", "label", "translation_engine", "engine_model",
                             "reflect", "auto_qc"}


# ---- Parity T03/T04/X13: import, CSV export, bulk delete ---------------------

class TestImportExportBulk:
    CSV = "term,translation,category\n沈清疑,Shen Qingyi,person_name\n师姐,Senior Sister,bogus\n"

    def test_import_csv_adds_and_reports(self, client, isolated_db):
        did = _drama(isolated_db)
        r = client.post(_url(did, "import"), json={"text": self.CSV, "filename": "g.csv"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert sorted(body["added"]) == ["师姐", "沈清疑"]
        assert body["overwritten"] == [] and body["skipped_existing"] == []
        assert any("unknown category" in w for w in body["warnings"])
        terms = {t["term_original"]: t for t in client.get(_url(did, "terms")).json()}
        assert terms["沈清疑"]["category"] == "person_name"
        assert terms["师姐"]["category"] == "other"

    def test_import_tsv_and_json(self, client, isolated_db):
        did = _drama(isolated_db)
        r = client.post(_url(did, "import"), json={"text": "a\tA\nb\tB\n", "filename": "x.tsv"})
        assert sorted(r.json()["added"]) == ["a", "b"]
        r = client.post(_url(did, "import"), json={
            "text": '[{"term_original": "c", "term_translation": "C", "enforce_exact": "yes"}]'})
        assert r.json()["added"] == ["c"]
        c = next(t for t in client.get(_url(did, "terms")).json() if t["term_original"] == "c")
        assert c["enforce_exact"] is True

    def test_existing_terms_skipped_unless_confirmed_overwrite(self, client, isolated_db):
        did = _drama(isolated_db)
        client.post(_url(did, "terms"), json=FULL)
        text = "term,translation\n沈清疑,Changed\n"
        r = client.post(_url(did, "import"), json={"text": text})
        assert r.json()["skipped_existing"] == ["沈清疑"]
        r = client.post(_url(did, "import"), json={"text": text, "overwrite_existing": True})
        assert r.status_code == 422
        r = client.post(_url(did, "import"),
                        json={"text": text, "overwrite_existing": True, "confirm": True})
        assert r.json()["overwritten"] == ["沈清疑"]
        t = client.get(_url(did, "terms")).json()[0]
        assert t["term_translation"] == "Changed"
        assert t["aliases"] == FULL["aliases"]   # kept on overwrite

    def test_import_without_overwrite_never_replaces_a_term_added_meanwhile(
            self, client, isolated_db, monkeypatch):
        did = _drama(isolated_db)
        sid = isolated_db.get_drama(did)["series_id"]
        real_list = isolated_db.list_glossary_terms

        def list_then_race(series_id):
            rows = real_list(series_id)
            isolated_db.upsert_glossary_term(sid, "沈清疑", "Added meanwhile")
            return rows

        monkeypatch.setattr(isolated_db, "list_glossary_terms", list_then_race)
        r = client.post(_url(did, "import"), json={"text": "term,translation\n沈清疑,Import\nb,B\n"})
        monkeypatch.setattr(isolated_db, "list_glossary_terms", real_list)
        assert r.status_code == 200, r.text
        assert r.json()["added"] == ["b"] and r.json()["skipped_existing"] == ["沈清疑"]
        terms = {t["term_original"]: t["term_translation"] for t in real_list(sid)}
        assert terms == {"沈清疑": "Added meanwhile", "b": "B"}

    @pytest.mark.parametrize("text", ["", "[]", "{}", "not json [", '[{"term_original": 5}]',
                                      '[{"term_original": "x", "category": ["a"]}]'])
    def test_bad_imports_are_422_and_write_nothing(self, client, isolated_db, text):
        did = _drama(isolated_db)
        r = client.post(_url(did, "import"), json={"text": text or "", "filename": "g.json"})
        assert r.status_code == 422
        assert client.get(_url(did, "terms")).json() == []

    @pytest.mark.parametrize("text", ["term,translation\na," + "x" * 140_000 + "\n",
                                      "[" * 100_000 + "]" * 100_000],
                             ids=["huge_csv_field", "deep_json"])
    def test_parser_limits_are_422_not_500(self, client, isolated_db, text):
        did = _drama(isolated_db)
        r = client.post(_url(did, "import"), json={"text": text})
        assert r.status_code == 422, r.text
        assert client.get(_url(did, "terms")).json() == []

    def test_import_too_long_term_reported_invalid(self, client, isolated_db):
        did = _drama(isolated_db)
        text = "term,translation\n" + "x" * 300 + ",X\nok,OK\n"
        body = client.post(_url(did, "import"), json={"text": text}).json()
        assert body["added"] == ["ok"] and len(body["invalid"]) == 1

    def test_import_needs_series_and_drama(self, client, isolated_db):
        did = _drama(isolated_db, series=False)
        assert client.post(_url(did, "import"), json={"text": "a,b"}).status_code == 400
        assert client.post(_url(999, "import"), json={"text": "a,b"}).status_code == 404
        assert client.post(_url(did, "import"), json={"text": "a,b", "x": 1}).status_code == 422

    def test_export_csv(self, client, isolated_db):
        did = _drama(isolated_db)
        client.post(_url(did, "terms"), json=FULL)
        r = client.get(_url(did, "export.csv"))
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        assert f'glossary_{did}.csv' in r.headers["content-disposition"]
        assert "沈清疑,Shen Qingyi,person_name,keep_pinyin,yes,hero" in r.text
        # round-trips through the importer
        other = _drama(isolated_db, "T")
        assert client.post(_url(other, "import"), json={"text": r.text}).json()["added"] == ["沈清疑"]

    @pytest.mark.parametrize("lead", ["=", "+", "-", "@"])
    def test_export_csv_neutralises_formulas_and_round_trips(self, client, isolated_db, lead):
        did = _drama(isolated_db)
        term = {"term_original": f"{lead}HYPERLINK(1)", "term_translation": f"{lead}cmd|x",
                "notes": f"{lead}1+1"}
        assert client.post(_url(did, "terms"), json=term).status_code == 200
        text = client.get(_url(did, "export.csv")).text
        import csv, io
        row = list(csv.reader(io.StringIO(text)))[1]
        for cell in row:
            assert not cell.startswith(("=", "+", "-", "@", "\t", "\r")), row
        assert row[0] == f"'{lead}HYPERLINK(1)" and row[5] == f"'{lead}1+1"
        other = _drama(isolated_db, "T")
        assert client.post(_url(other, "import"), json={"text": text}).status_code == 200
        t = client.get(_url(other, "terms")).json()[0]
        assert (t["term_original"], t["term_translation"], t["notes"]) == (
            term["term_original"], term["term_translation"], term["notes"])

    def test_import_keeps_a_quote_not_before_a_formula_char(self, client, isolated_db):
        did = _drama(isolated_db)
        client.post(_url(did, "import"), json={"text": "term,translation\n'tis,'Twas\n"})
        t = client.get(_url(did, "terms")).json()[0]
        assert (t["term_original"], t["term_translation"]) == ("'tis", "'Twas")

    def test_export_csv_empty_and_unknown(self, client, isolated_db):
        did = _drama(isolated_db, series=False)
        r = client.get(_url(did, "export.csv"))
        assert r.status_code == 200 and r.text.strip().startswith("term_original")
        assert client.get(_url(999, "export.csv")).status_code == 404

    def test_bulk_delete(self, client, isolated_db):
        did = _drama(isolated_db)
        ids = [client.post(_url(did, "terms"), json={"term_original": t, "term_translation": t})
               .json()["id"] for t in ("a", "b", "c")]
        other = _drama(isolated_db, "Other")
        foreign = client.post(_url(other, "terms"),
                              json={"term_original": "z", "term_translation": "z"}).json()["id"]
        assert client.post(_url(did, "terms/bulk-delete"),
                           json={"term_ids": ids[:2]}).status_code == 422
        r = client.post(_url(did, "terms/bulk-delete"),
                        json={"term_ids": [ids[0], ids[1], foreign, 12345], "confirm": True})
        assert r.status_code == 200
        assert r.json() == {"deleted": ids[:2], "not_found": [foreign, 12345]}
        assert [t["id"] for t in client.get(_url(did, "terms")).json()] == [ids[2]]
        assert len(client.get(_url(other, "terms")).json()) == 1

    @pytest.mark.parametrize("body", [{"term_ids": [], "confirm": True},
                                      {"term_ids": ["1"], "confirm": True},
                                      {"term_ids": [1], "confirm": "yes"}])
    def test_bulk_delete_bad_body(self, client, isolated_db, body):
        did = _drama(isolated_db)
        assert client.post(_url(did, "terms/bulk-delete"), json=body).status_code == 422


# ---- M2: overwriting on import is PC-only until network zones exist ---------

def _household_headers():
    from api import auth as api_auth
    from services import auth_service
    u = auth_service.add_user("kid@example.com")
    assert "lines.edit" in auth_service.effective_permissions(u["id"])
    s = auth_service.create_session(u["id"])
    return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
            api_auth.CSRF_HEADER: s["csrf_token"]}


def test_import_overwrite_is_pc_only_when_auth_on(isolated_db):
    did = _drama(isolated_db)
    isolated_db.upsert_glossary_term(isolated_db.get_drama(did)["series_id"], "沈清疑", "Old")
    h = _household_headers()
    remote = TestClient(create_app(ApiSettings(auth_mode="on")),
                        base_url="https://baihe.example.com", raise_server_exceptions=False)
    text = "term,translation\n沈清疑,Changed\n师姐,Senior Sister\n"
    r = remote.post(_url(did, "import"), headers=h,
                    json={"text": text, "overwrite_existing": True, "confirm": True})
    assert r.status_code == 403
    assert [t["term_original"] for t in isolated_db.list_glossary_terms(
        isolated_db.get_drama(did)["series_id"])] == ["沈清疑"]
    # pasting new terms stays allowed for lines.edit away from the PC
    r = remote.post(_url(did, "import"), headers=h, json={"text": text})
    assert r.status_code == 200, r.text
    assert r.json()["added"] == ["师姐"] and r.json()["skipped_existing"] == ["沈清疑"]
    local = TestClient(create_app(ApiSettings(auth_mode="on")), base_url="http://127.0.0.1:8600",
                       client=("127.0.0.1", 5000), raise_server_exceptions=False)
    r = local.post(_url(did, "import"), headers=h,
                   json={"text": text, "overwrite_existing": True, "confirm": True})
    assert r.status_code == 200, r.text
    assert r.json()["overwritten"] == ["沈清疑", "师姐"]
    sid = isolated_db.get_drama(did)["series_id"]
    assert {t["term_original"]: t["term_translation"]
            for t in isolated_db.list_glossary_terms(sid)}["沈清疑"] == "Changed"
