"""
tests/test_reader_no_segmenter.py -- the Reader still renders every line on a
core-only install, where the optional segmentation packages (jieba/pypinyin,
sudachipy/pykakasi, kiwipiepy) are missing. Those packages are made
unimportable with a sys.modules block (None entries raise ImportError);
nothing is uninstalled.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import db
import segment
from core import Line

BLOCKED = ("jieba", "pypinyin", "sudachipy", "pykakasi", "kiwipiepy")


@pytest.fixture
def no_segmenter(monkeypatch):
    for name in BLOCKED:
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.setattr(segment, "_jieba", None)
    monkeypatch.setattr(segment, "_sudachi_tokenizer", None)


@pytest.mark.parametrize("lang", ["zh", "ja", "ko"])
def test_segmentation_unavailable_without_packages(no_segmenter, lang):
    assert segment.segmentation_available(lang) is False


def test_per_character_tokens_reassemble():
    assert segment.per_character_tokens("你好 x") == [("你", None), ("好", None), (" ", None), ("x", None)]


def test_reader_page_renders_every_line_without_jieba(isolated_db, no_segmenter):
    from services import reader_service
    did = db.create_drama(title_en="NoJieba", source_language="zh")
    db.save_lines(did, [Line(idx=i, start=float(i), end=i + 1.0, zh=f"你好朋友{i}", en=f"hi {i}")
                        for i in range(3)])
    out = reader_service.get_reader_page(did, page=1, chapter_size=10)
    assert out["segmentation_available"] is False
    assert out["html"].count('class="line-row"') == 3
    assert 'data-word="你"' in out["html"] and "<rt>" not in out["html"]


def test_reader_api_reports_flag_without_jieba(isolated_db, no_segmenter):
    from fastapi.testclient import TestClient
    from api.api_config import ApiSettings
    from api.server import create_app
    did = db.create_drama(title_en="NoJieba", source_language="zh")
    db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="hi")])
    client = TestClient(create_app(ApiSettings()), base_url="http://127.0.0.1:8600",
                        client=("127.0.0.1", 5000))
    r = client.get(f"/api/reader/dramas/{did}/page")
    assert r.status_code == 200, r.text
    assert r.json()["segmentation_available"] is False
