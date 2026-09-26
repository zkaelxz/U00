"""
tests/test_reader_tab.py -- Reader tab's Line tools.

Step 25c item 3: "Improve this line" and "Re-transcribe" stored their
generated result under a drama-scoped key, so a result generated for one
line was still offered -- and applied -- after picking a different line.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core as core_module
import line_tools
import segment
import translate_engines
from core import Line


@pytest.fixture(autouse=True)
def _no_jieba(monkeypatch):
    """The reader's word segmentation needs jieba, an optional extra --
    faked here so a core-only install still runs these tests."""
    monkeypatch.setattr(segment, "segment_and_annotate",
                         lambda text, language, chinese_script="simplified": [(text, "")])


def _drama(isolated_db, audio=False):
    did = isolated_db.create_drama(title_en="Reader Drama", status="translated",
                                   audio_filename="audio.wav" if audio else None)
    isolated_db.save_lines(did, [
        Line(idx=0, start=0.0, end=1.0, zh="第一行", en="Line one"),
        Line(idx=1, start=1.0, end=2.0, zh="第二行", en="Line two"),
    ])
    if audio:
        ddir = isolated_db.drama_dir(did)
        os.makedirs(ddir, exist_ok=True)
        open(os.path.join(ddir, "audio.wav"), "wb").close()
    return did


def _run(did):
    from streamlit.testing.v1 import AppTest

    def _render():
        import tabs.reader_tab as rt
        rt.render_reader_tab()

    at = AppTest.from_function(_render)
    at.session_state[f"story_key_{did}"] = "test-key"
    at.run(timeout=30)
    return at


def _pick_line(at, did, number):
    [picker] = [s for s in at.selectbox if s.key == f"lt_{did}"]
    [label] = [o for o in picker.options if o.startswith(f"Line {number}:")]
    picker.set_value(label).run(timeout=30)


def _click(at, label, key=None):
    [button] = [b for b in at.button if b.label == label and (key is None or b.key == key)]
    button.click().run(timeout=30)


class TestImproveThisLineIsPerLine:
    @pytest.fixture(autouse=True)
    def _fake_llm(self, monkeypatch):
        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: object())
        monkeypatch.setattr(line_tools, "improve_line",
                             lambda zh, en, eng, issue="", source_language="zh": f"better {en}")

    def test_a_result_for_one_line_is_not_offered_on_another(self, isolated_db):
        did = _drama(isolated_db)
        at = _run(did)
        _click(at, "Rewrite line")
        assert any(s.value == "better Line one" for s in at.success)

        _pick_line(at, did, 2)
        assert not any(s.value == "better Line one" for s in at.success)
        assert not [b for b in at.button if b.label == "Apply to this line"]
        assert [ln.en for ln in isolated_db.load_line_objects(did)] == ["Line one", "Line two"]

    def test_going_back_applies_the_result_to_the_line_it_was_made_for(self, isolated_db):
        did = _drama(isolated_db)
        at = _run(did)
        _click(at, "Rewrite line")
        _pick_line(at, did, 2)
        _pick_line(at, did, 1)
        _click(at, "Apply to this line")

        assert [ln.en for ln in isolated_db.load_line_objects(did)] == ["better Line one", "Line two"]


class TestRetranscribeIsPerLine:
    def test_a_result_for_one_line_is_not_applied_to_another(self, isolated_db, monkeypatch):
        monkeypatch.setattr(core_module, "extract_audio_slice",
                             lambda audio, start, end, out: open(out, "wb").close())
        monkeypatch.setattr(core_module, "transcribe_for_timing",
                             lambda path, **k: [{"text": "重听的第一行"}])
        did = _drama(isolated_db, audio=True)
        at = _run(did)
        _click(at, "Re-transcribe")
        assert any(s.value == "重听的第一行" for s in at.success)

        _pick_line(at, did, 2)
        assert not any(s.value == "重听的第一行" for s in at.success)
        assert not [b for b in at.button if b.key and b.key.startswith("apply_retranscribe_")]
        assert [ln.zh for ln in isolated_db.load_line_objects(did)] == ["第一行", "第二行"]
