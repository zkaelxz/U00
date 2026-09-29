"""
tests/test_reader_no_segmenter.py -- the Reader on a core-only install,
where the optional word-splitting packages (jieba/pypinyin, sudachipy/
pykakasi, kiwipiepy) are missing. Unlike test_reader_service.py's
monkeypatched segment_and_annotate, these tests make the packages really
unimportable with a sys.modules block (None entries raise ImportError), so
the real import path in segment.py runs; nothing is uninstalled.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import db
import segment
from core import Line
from services.service_errors import DependencyUnavailableError

BLOCKED = ("jieba", "pypinyin", "sudachipy", "pykakasi", "kiwipiepy")


@pytest.fixture
def no_segmenter(monkeypatch):
    for name in BLOCKED:
        monkeypatch.setitem(sys.modules, name, None)
    for cached in ("_jieba", "_sudachi_tokenizer", "_kakasi", "_kiwi"):
        monkeypatch.setattr(segment, cached, None)


def _drama(lang="zh", n=3):
    did = db.create_drama(title_en="NoSplitter", source_language=lang)
    db.save_lines(did, [Line(idx=i, start=float(i), end=i + 1.0, zh=f"你好朋友{i}", en=f"hi {i}")
                        for i in range(n)])
    return did


def _client():
    from fastapi.testclient import TestClient
    from api.api_config import ApiSettings
    from api.server import create_app
    return TestClient(create_app(ApiSettings()), base_url="http://127.0.0.1:8600",
                      client=("127.0.0.1", 5000), raise_server_exceptions=False)


@pytest.mark.parametrize("lang", ["zh", "ja", "ko"])
def test_reader_page_renders_every_line_unsplit(isolated_db, no_segmenter, lang):
    from services import reader_service
    out = reader_service.get_reader_page(_drama(lang), page=1, chapter_size=10)
    html_str = out["html"]
    assert html_str.count('class="line-row"') == 3
    assert "你好朋友0" in html_str and "hi 2" in html_str
    assert 'class="segmenter-note"' in html_str
    assert 'class="word"' not in html_str


def test_reader_page_route_works_without_splitter(isolated_db, no_segmenter):
    did = _drama(n=1)
    r = _client().get(f"/api/reader/dramas/{did}/page")
    assert r.status_code == 200, r.text
    assert 'class="segmenter-note"' in r.json()["html"]


def test_lookup_names_the_missing_package(isolated_db, no_segmenter):
    from services import reader_service
    did = _drama()
    with pytest.raises(DependencyUnavailableError) as ei:
        reader_service.lookup_page_definitions(did, 1)
    assert "jieba" in str(ei.value) and "engine call failed" not in str(ei.value)
    assert db.list_vocab_lookups(did) == []


def test_lookup_route_is_503_without_splitter(isolated_db, no_segmenter):
    did = _drama(n=1)
    r = _client().post(f"/api/reader/dramas/{did}/lookup", json={"page": 1})
    assert r.status_code == 503, r.text
    assert "jieba" in r.text and "engine call failed" not in r.text
