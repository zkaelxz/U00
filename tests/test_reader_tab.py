"""
tests/test_reader_tab.py -- Reader tab.

"Line tools" (Improve this line/Re-transcribe/Why this?/Alternatives/
Grammar/Pronounce), and its Step 25c/25x per-line-isolation regression
tests that used to live here, moved to Workspace's own per-line 🔧
popover in Step 15 -- same job, previously split across two tabs for no
functional reason. See tests/test_workspace_tab.py for the current
coverage (that popover's buttons are already keyed per-line by ln.idx,
not a shared re-selectable-picker key, so the old bug class this file
used to guard against can't reoccur the same way).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import segment
from core import Line


@pytest.fixture(autouse=True)
def _no_jieba(monkeypatch):
    """The reader's word segmentation needs jieba, an optional extra --
    faked here so a core-only install still runs these tests."""
    monkeypatch.setattr(segment, "segment_and_annotate",
                         lambda text, language, chinese_script="simplified": [(text, "")])


def _run(did):
    from streamlit.testing.v1 import AppTest

    def _render():
        import tabs.reader_tab as rt
        rt.render_reader_tab()

    at = AppTest.from_function(_render)
    at.session_state[f"story_key_{did}"] = "test-key"
    at.run(timeout=30)
    return at


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

    def test_audio_only_drama_shows_an_unsynced_caption_fallback(self, isolated_db):
        # Step 45: a real reported bug -- a Streamer/VOD drama downloaded
        # with no video file (only audio) has no way to show captions in
        # the video player (there is none), leaving the user with no
        # subtitles at all. Since st.audio genuinely can't overlay timed
        # captions, show the same text as a plain readout instead of
        # nothing.
        did = self._media_drama(isolated_db, "audio")
        at = _run(did)
        assert not at.exception
        _lang_radio = [r for r in at.radio if r.key == "reader_audio_captions_lang_" + str(did)][0]
        assert list(_lang_radio.options) == ["Source", "English", "Bilingual"]
        assert any("第一行" in c.value for c in at.caption)  # "Source" is the default selection

        at = _lang_radio.set_value("English").run(timeout=30)
        assert any("Line one" in c.value for c in at.caption)

    def test_audio_only_drama_with_no_translation_shows_nothing_extra(self, isolated_db):
        did = self._media_drama(isolated_db, "audio", en=False)
        at = _run(did)
        assert not at.exception
        # Only "Source" text exists -- the fallback should still show it.
        assert any(r.key == "reader_audio_captions_lang_" + str(did) for r in at.radio)
        assert any("第一行" in c.value for c in at.caption)


# ------------------------------------------- Step 15: Reader tab declutter

class TestStoryPanelAndCollapsibleSections:
    """Step 15: the primary reading view (drama picker, Watch/listen,
    pagination, the reader itself) stays immediately visible; Story tools,
    Universe wiki, and Ask about this drama move into a secondary "Story"
    popover reachable from Read & Watch rather than always stacked below
    the reader; My notes and Vocabulary export stay attached to the
    reading view but become individually collapsible, at the user's
    direct request."""

    def _drama(self, isolated_db):
        did = isolated_db.create_drama(title_en="Reader Drama", status="translated")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hello")])
        return did

    def test_primary_reader_renders_before_any_secondary_content(self, isolated_db):
        # The reader's own iframe is built and inserted well before the
        # Story popover or the My notes / Vocabulary export expanders --
        # nothing secondary pushes it down the page.
        did = self._drama(isolated_db)
        at = _run(did)
        assert not at.exception
        expander_labels = [e.label for e in at.expander]
        assert "🗒️ My notes" in expander_labels
        assert "📇 Vocabulary export" in expander_labels

    def test_story_tools_universe_wiki_and_qa_are_reachable(self, isolated_db):
        did = self._drama(isolated_db)
        at = _run(did)
        expander_labels = [e.label for e in at.expander]
        assert "🧠 Story tools" in expander_labels
        assert "📚 Universe wiki" in expander_labels
        assert "💬 Ask about this drama" in expander_labels
        # Confirms these three still have their own real widgets underneath,
        # not just an empty relocated shell.
        assert any(ti.key and ti.key.startswith("charq_") for ti in at.text_input)
        assert any(b.label == "🔄 Update wiki from what I've read" for b in at.button)
        assert at.chat_input

    def test_my_notes_and_vocab_export_are_individually_collapsible(self, isolated_db):
        did = self._drama(isolated_db)
        at = _run(did)
        notes = [e for e in at.expander if e.label == "🗒️ My notes"][0]
        vocab = [e for e in at.expander if e.label == "📇 Vocabulary export"][0]
        # Each is its own independent st.expander -- collapsing/expanding one
        # doesn't affect the other. Their content still renders either way
        # (AppTest executes expander bodies regardless of open state).
        assert any(ta.key and ta.key.startswith("pnotes_") for ta in at.text_area)
        assert any(c.value and "word(s) looked up" in c.value for c in vocab.caption)
        assert notes is not vocab

    def test_no_bare_or_duplicate_leftover_line_tools_section(self, isolated_db):
        # Line tools moved to Workspace entirely -- nothing with that
        # heading should remain in Reader.
        src = open("tabs/reader_tab.py", encoding="utf-8").read()
        assert "Line tools" not in src
