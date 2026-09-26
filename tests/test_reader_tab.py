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


# ------------------------------------------- Step 12: live captions in Watch / listen

class TestCaptionTracks:
    """Step 12 item 7: the Watch / listen video offers CC tracks built from
    the drama's current lines -- but only for languages that actually have
    text, and never for an audio-only drama (st.audio has no subtitles)."""

    def test_both_sides_filled_gives_three_tracks(self):
        import tabs.reader_tab as rt
        lines = [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hello")]
        tracks = rt.caption_tracks(lines)
        assert list(tracks) == ["Source", "English", "Bilingual"]
        assert "你好" in tracks["Source"] and "Hello" not in tracks["Source"]
        assert "Hello" in tracks["English"]
        assert "Hello\n你好" in tracks["Bilingual"]

    def test_untranslated_drama_gets_source_only(self):
        import tabs.reader_tab as rt
        lines = [Line(idx=0, start=0.0, end=1.0, zh="你好", en=""),
                 Line(idx=1, start=1.0, end=2.0, zh="再见", en="   ")]
        assert list(rt.caption_tracks(lines)) == ["Source"]

    def test_no_text_at_all_gives_no_tracks(self):
        import tabs.reader_tab as rt
        assert rt.caption_tracks([Line(idx=0, start=0.0, end=1.0, zh=" ", en="")]) == {}

    def _media_drama(self, isolated_db, kind, en=True):
        field = "source_video_filename" if kind == "video" else "audio_filename"
        name = "video.mp4" if kind == "video" else "audio.wav"
        did = isolated_db.create_drama(title_en="Media Drama", status="translated",
                                       **{field: name})
        isolated_db.save_lines(did, [
            Line(idx=0, start=0.0, end=1.0, zh="第一行", en="Line one" if en else ""),
        ])
        ddir = isolated_db.drama_dir(did)
        os.makedirs(ddir, exist_ok=True)
        with open(os.path.join(ddir, name), "wb") as f:
            f.write(b"\x00" * 16)
        return did

    def test_video_player_gets_the_tracks(self, isolated_db):
        did = self._media_drama(isolated_db, "video")
        at = _run(did)
        assert not at.exception
        [video] = at.get("video")
        assert [t.label for t in video.proto.subtitles] == ["Source", "English", "Bilingual"]

    def test_video_player_skips_an_empty_language(self, isolated_db):
        did = self._media_drama(isolated_db, "video", en=False)
        at = _run(did)
        [video] = at.get("video")
        assert [t.label for t in video.proto.subtitles] == ["Source"]

    def test_audio_only_drama_has_no_subtitles(self, isolated_db):
        did = self._media_drama(isolated_db, "audio")
        at = _run(did)
        assert not at.exception
        assert at.get("video") == []
        assert len(at.get("audio")) == 1
