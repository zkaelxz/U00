"""Translation memory as TMX: GET /api/series/{id}/translation-memory.tmx and POST .../import."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import db
from tests.api_helpers import loopback_client
from api.api_config import ApiSettings
from api.server import create_app
from services import tm_service


@pytest.fixture
def client(isolated_db):
    app = create_app(ApiSettings(auth_mode="off", serve_frontend=False))
    return loopback_client(app)


@pytest.fixture
def series(isolated_db):
    sid = db.create_series("Saga")
    db.record_translation_memory(sid, "你好 <世界>", "Hello & goodbye")
    db.record_translation_memory(sid, "再见", "Bye")
    db.record_translation_memory(sid, "再见", "Bye")
    return sid


def _tmx(body, srclang="zh"):
    return f'<?xml version="1.0"?><tmx version="1.4"><header srclang="{srclang}"/><body>{body}</body></tmx>'


def _tu(src, dst, uses=None, sl="zh", dl="en"):
    u = f' usagecount="{uses}"' if uses else ""
    return f'<tu{u}><tuv xml:lang="{sl}"><seg>{src}</seg></tuv><tuv xml:lang="{dl}"><seg>{dst}</seg></tuv></tu>'


def test_export_then_import_round_trips_into_another_series(client, series):
    r = client.get(f"/api/series/{series}/translation-memory.tmx")
    assert r.status_code == 200 and "x-tmx+xml" in r.headers["content-type"]
    assert 'usagecount="2"' in r.text and "Hello &amp; goodbye" in r.text
    other = db.create_series("Copy")
    res = client.post(f"/api/series/{other}/translation-memory/import", json={"tmx": r.text})
    assert res.status_code == 200 and res.json() == {"series_id": other, "added": 2, "skipped": 0}
    rows = {m["source_text"]: (m["translation"], m["use_count"]) for m in db.list_translation_memory(other)}
    assert rows == {"你好 <世界>": ("Hello & goodbye", 1), "再见": ("Bye", 2)}


def test_import_keeps_existing_translation_and_larger_use_count(client, series):
    body = _tmx(_tu("再见", "Bye", 9) + _tu("你好 &lt;世界&gt;", "Different", 5) + _tu("新", "New"))
    res = client.post(f"/api/series/{series}/translation-memory/import", json={"tmx": body})
    assert res.json()["added"] == 1 and res.json()["skipped"] == 2
    rows = {m["source_text"]: (m["translation"], m["use_count"]) for m in db.list_translation_memory(series)}
    assert rows["再见"] == ("Bye", 9)
    assert rows["你好 <世界>"][0] == "Hello & goodbye"


def test_import_reads_the_pair_in_either_order_and_skips_odd_units(client, series):
    body = _tmx(_tu("Hi", "你好呀", sl="en", dl="zh") + _tu("a", "b", dl="fr", sl="de")
                + '<tu><tuv xml:lang="zh"><seg>只有一个</seg></tuv></tu>')
    res = client.post(f"/api/series/{series}/translation-memory/import", json={"tmx": body})
    assert res.json()["added"] == 1
    assert "你好呀" in {m["source_text"] for m in db.list_translation_memory(series)}


@pytest.mark.parametrize("bad", [
    '<?xml version="1.0"?><!DOCTYPE tmx [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><tmx><body/></tmx>',
    "not xml at all", "<html/>",
])
def test_hostile_or_invalid_xml_is_422_and_adds_nothing(client, series, bad):
    before = len(db.list_translation_memory(series))
    res = client.post(f"/api/series/{series}/translation-memory/import", json={"tmx": bad})
    assert res.status_code == 422
    assert len(db.list_translation_memory(series)) == before


def test_oversize_body_is_422(client, series):
    res = client.post(f"/api/series/{series}/translation-memory/import", json={"tmx": "x" * 5_000_001})
    assert res.status_code == 422


def test_too_many_entries_is_refused(series, monkeypatch):
    monkeypatch.setattr(tm_service, "MAX_ENTRIES", 2)
    with pytest.raises(Exception):
        tm_service.parse_tmx(_tmx(_tu("a", "1") + _tu("b", "2") + _tu("c", "3")))


def test_unknown_series_is_404(client):
    assert client.get("/api/series/999/translation-memory.tmx").status_code == 404
    assert client.post("/api/series/999/translation-memory/import", json={"tmx": _tmx("")}).status_code == 404
