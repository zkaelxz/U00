"""Offering and accepting burned-in caption text for lines the transcript spelled differently."""

import db
from core import Line
from services import hardsub_caption_service as captions
from services import hardsub_speaker_read_service as reading
from tests.test_hardsub_speaker_apply import _outcome

GROUPS = [{"key": "yellow-right", "lines": [
    {"start": 0.0, "end": 3.0, "text": "亞洛赫你好嗎"},
    {"start": 3.0, "end": 6.0, "text": "完全不同的話題呢"},
    {"start": 6.0, "end": 9.0, "text": "今天天氣很好"}]}]


def _title():
    did = db.create_drama(title_en="Stream", source_language="zh")
    db.save_lines(did, [Line(0, 0.0, 3.0, "亞羅赫你好嗎"), Line(1, 3.0, 6.0, "我們吃飯吧"),
                        Line(2, 6.0, 9.0, "今天天氣很好")])
    reading._save_result(did, _outcome(GROUPS))
    return did


def test_only_a_close_but_different_line_is_offered(isolated_db):
    got = captions.suggestions(None, _title())["suggestions"]
    assert [(s["current"], s["caption"]) for s in got] == [("亞羅赫你好嗎", "亞洛赫你好嗎")]
    assert got[0]["group"] == "yellow-right"


def test_accepting_writes_the_caption_and_skips_lines_not_offered(isolated_db):
    did = _title()
    ids = [r["id"] for r in db.load_lines(did)]
    got = captions.accept(None, did, [ids[0], ids[1]])
    assert got == {"changed": 1, "skipped": 1}
    assert [r["zh"] for r in db.load_lines(did)][:2] == ["亞洛赫你好嗎", "我們吃飯吧"]
