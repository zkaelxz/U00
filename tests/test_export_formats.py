"""
tests/test_export_formats.py -- Step 6b: VTT and ASS export with real
styling, long-line wrapping, overlap clamping, reading-speed checks, and
hardsub styling (ASS burned directly, no force_style).
"""
import re

import pytest

import subtitle_formats as sf
import video_export
from core import Line, lines_to_srt, lines_to_bilingual_srt


def _lines():
    return [Line(idx=0, start=0.0, end=2.5, zh="你好", en="Hello there.", speaker="SPEAKER_00"),
            Line(idx=1, start=3.0, end=5.25, zh="再见", en="Goodbye.", speaker="SPEAKER_01")]


GOLDEN_SRT = ("1\n00:00:00,000 --> 00:00:02,500\nHello there.\n\n"
              "2\n00:00:03,000 --> 00:00:05,250\nGoodbye.\n")


class TestSrtUnchanged:
    def test_default_srt_export_is_byte_identical(self):
        """What the export panel produces with the new controls untouched
        (no wrapping, no overlaps) is exactly the old SRT."""
        lines = _lines()
        export_lines, overlaps = sf.clamp_overlaps(lines)
        assert overlaps == []
        assert lines_to_srt(sf.wrap_lines(export_lines, None), "en") == lines_to_srt(lines, "en") == GOLDEN_SRT
        assert (lines_to_bilingual_srt(sf.wrap_lines(export_lines, None))
                == lines_to_bilingual_srt(lines))


class TestVtt:
    def test_header_and_timestamps(self):
        vtt = sf.lines_to_vtt(_lines())
        assert vtt.startswith("WEBVTT\n\n")
        assert "00:00:00.000 --> 00:00:02.500\nHello there." in vtt
        assert "00:00:03.000 --> 00:00:05.250" in vtt

    def test_bilingual_and_arrow_safety(self):
        lines = _lines()
        lines[0].en = "a --> b"
        vtt = sf.lines_to_vtt(lines, "bilingual")
        assert "a -> b\n你好" in vtt


class TestAss:
    def test_style_and_one_colour_per_speaker(self):
        style = dict(sf.ASS_PRESETS["Streamer clip"], font="Meiryo", size=33, outline_width=5)
        colors = {"SPEAKER_00": "#FF0000", "SPEAKER_01": "#00FF00"}
        ass = sf.lines_to_ass(_lines(), style, speaker_colors=colors,
                              speaker_names={"SPEAKER_00": "Xiaoling"})
        styles = {l.split(",")[0][7:]: l.split(",") for l in ass.splitlines() if l.startswith("Style: ")}
        assert styles["Speaker 1"][1:3] == ["Meiryo", "33"]
        assert styles["Speaker 1"][3] == "&H000000FF"   # red, in ASS's BGR order
        assert styles["Speaker 2"][3] == "&H0000FF00"
        assert styles["Speaker 1"][16] == "5"           # outline width
        dialogue = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
        assert dialogue[0].split(",")[3:5] == ["Speaker 1", "Xiaoling"]
        assert dialogue[1].split(",")[3] == "Speaker 2"
        assert "0:00:00.00,0:00:02.50" in dialogue[0]

    def test_text_is_escaped(self):
        lines = _lines()
        lines[0].en = "a {\\b1} trick\nsecond line"
        ass = sf.lines_to_ass(lines, sf.ASS_PRESETS["Clean"])
        assert "a \\{\\\\b1\\} trick\\Nsecond line" in ass

    def test_presets_differ_and_clean_is_the_old_hardsub_look(self):
        assert sf.ASS_PRESETS["Clean"]["size"] == 24 and sf.ASS_PRESETS["Clean"]["outline_width"] == 2
        assert sf.ASS_PRESETS["Streamer clip"]["bold"] is True
        assert sf.ASS_PRESETS["Streamer clip"]["outline_width"] > sf.ASS_PRESETS["Clean"]["outline_width"]

    def test_speaker_colours_are_distinct_by_default(self):
        colors = sf.default_speaker_colors(["A", "B", "C", None])
        assert len(set(colors.values())) == 3 and None not in colors


class TestLongLines:
    def test_splits_at_a_clause_boundary(self):
        text = "I told you already, you should never go back there alone."
        wrapped = sf.wrap_text(text, 30)
        assert wrapped.split("\n")[0] == "I told you already,"

    def test_never_splits_mid_word(self):
        text = "The extraordinarily complicated circumstances surrounding everything"
        for line in sf.wrap_text(text, 20).split("\n"):
            assert line in text and all(w in text.split() for w in line.split())
        assert sf.wrap_text("Supercalifragilisticexpialidocious", 10) == "Supercalifragilisticexpialidocious"

    def test_cjk_splits_at_punctuation(self):
        wrapped = sf.wrap_text("今天天气很好，我们一起去公园散步吧。然后再去吃饭好不好", 16)
        assert wrapped.split("\n")[0] == "今天天气很好，"
        assert all(len(l) <= 16 for l in wrapped.split("\n"))

    def test_tighter_limit_for_cjk(self):
        assert sf.line_char_limit("zh") < sf.line_char_limit("en")

    def test_wrapping_applies_to_every_format(self):
        lines = [Line(idx=0, start=0, end=5, zh="你好", en="I told you already, you should never go back.")]
        wrap = {"en": 25, "zh": 16}
        assert "already,\nyou" in lines_to_srt(sf.wrap_lines(lines, wrap), "en")
        assert "already,\nyou" in sf.lines_to_vtt(lines, "en", wrap_chars=wrap)
        assert "already,\\Nyou" in sf.lines_to_ass(lines, sf.ASS_PRESETS["Clean"], wrap_chars=wrap)


class TestOverlapClamp:
    def test_overlap_is_trimmed_in_the_export_copy_only(self):
        lines = _lines()
        lines[0].end = 3.4  # a manual edit made it run into the next line
        export_lines, overlaps = sf.clamp_overlaps(lines)
        assert overlaps == [0]
        assert export_lines[0].end == 3.0
        assert lines[0].end == 3.4  # the saved line itself isn't silently changed
        assert "00:00:03,000" in lines_to_srt(export_lines).split("\n\n")[0]

    def test_export_panel_warns_but_does_not_silently_flag_on_render(self, isolated_db):
        """Step 6d: detecting an overlap on every render is fine (read-only),
        but writing the flag used to happen unconditionally too -- straight
        from whatever st.session_state.lines held, with no user action. That's
        exactly how a stale-lines bug elsewhere could reach the database as a
        bogus flag before anyone noticed. Merely opening the page must not
        write anything."""
        from streamlit.testing.v1 import AppTest
        did = isolated_db.create_drama(title_en="D", media_type="audio_drama",
                                       content_mode="audio_drama", status="translated")
        lines = _lines()
        lines[0].end = 3.4
        isolated_db.save_lines(did, lines)

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()
        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        at.run(timeout=30)

        row = isolated_db.load_lines(did)[0]
        assert row["flag"] is None  # not written just from rendering the page
        assert row["end"] == 3.4
        assert any("overlap the next one" in w.value for w in at.warning)

        [b for b in at.button if b.key == f"flag_overlaps_{did}"][0].click()
        at.run(timeout=30)
        row = isolated_db.load_lines(did)[0]
        assert row["flag"] == sf.OVERLAP_FLAG and "3.00s" in row["flag_note"]
        assert row["end"] == 3.4


class TestReadingSpeed:
    def test_too_dense_is_flagged_normal_is_not(self):
        dense = Line(idx=0, start=0, end=1.0, zh="a", en="This translation is far too long to read in one second.")
        normal = Line(idx=1, start=1, end=4.0, zh="b", en="Nice to meet you.")
        assert [ln.idx for ln, _, _ in sf.dense_lines([dense, normal])] == [0]
        assert sf.flag_dense_lines([dense, normal]) == 1
        assert dense.flag == sf.READING_SPEED_FLAG and normal.flag is None

    def test_limits_follow_the_script(self):
        assert sf.script_of("你好世界") == "cjk" and sf.script_of("Hello") == "latin"
        assert sf.CPS_LIMITS["cjk"] < sf.CPS_LIMITS["latin"]
        # 12 CJK characters in 1.5s is 8/s: fine for English, too dense for CJK
        cjk = Line(idx=0, start=0, end=1.5, zh="", en="我们一起去公园散步吧好吗")
        assert sf.dense_lines([cjk])

    def test_an_existing_flag_is_never_replaced(self):
        ln = Line(idx=0, start=0, end=1, zh="a", en="Way too much text for a single second.",
                  flag="uncertain_translation")
        assert sf.flag_dense_lines([ln]) == 0 and ln.flag == "uncertain_translation"

    def test_translate_job_puts_dense_lines_in_the_review_queue(self, isolated_db, monkeypatch):
        import background_jobs
        import translate_engines
        from tabs.workspace_tab import run_translate_job
        did = isolated_db.create_drama(title_en="D")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好"),
                                     Line(idx=1, start=1, end=5, zh="再见")])
        lines = isolated_db.load_line_objects(did)

        def fake_translate(lines, engine, **kw):
            lines[0].en = "An extremely long translation that nobody could read in a second."
            lines[1].en = "Bye."
            kw["save_cb"](lines)
            return lines, []
        monkeypatch.setattr(translate_engines, "translate_lines_with_engine", fake_translate)
        job_id = "test_cps_translate"
        background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                          "error": None, "cancel_requested": False, "result": None}
        run_translate_job(job_id, did, lines, object(), {"id": did}, "", None, False, "en-US",
                          None, "", "claude", "audio_drama")
        background_jobs._jobs.pop(job_id, None)
        rows = isolated_db.load_lines(did)
        assert rows[0]["flag"] == sf.READING_SPEED_FLAG and rows[1]["flag"] is None


class TestHardsub:
    def _capture(self, monkeypatch):
        cmds = []
        monkeypatch.setattr(video_export.subprocess, "run", lambda cmd, **k: cmds.append(cmd))
        return cmds

    def test_burning_ass_skips_force_style(self, monkeypatch):
        cmds = self._capture(monkeypatch)
        video_export.burn_ass("in.mp4", sf.lines_to_ass(_lines(), sf.ASS_PRESETS["Clean"]), "out.mp4")
        vf = cmds[0][cmds[0].index("-vf") + 1]
        assert vf.startswith("subtitles='") and ".ass'" in vf
        assert "force_style" not in " ".join(cmds[0])

    def test_srt_burn_uses_the_chosen_style(self, monkeypatch):
        cmds = self._capture(monkeypatch)
        style = sf.ASS_PRESETS["Streamer clip"]
        video_export.burn_subtitles("in.mp4", GOLDEN_SRT, "out.mp4", font_size=style["size"],
                                    font_color=style["primary"], outline_color=style["outline"],
                                    font_name=style["font"], bold=True, outline_width=4, alignment=8)
        vf = cmds[0][cmds[0].index("-vf") + 1]
        for part in ("FontName=Arial Black", "FontSize=30", "Outline=4", "Bold=-1", "Alignment=8"):
            assert part in vf

    def test_default_srt_burn_keeps_the_old_look(self, monkeypatch):
        cmds = self._capture(monkeypatch)
        video_export.burn_subtitles("in.mp4", GOLDEN_SRT, "out.mp4")
        vf = cmds[0][cmds[0].index("-vf") + 1]
        assert "FontSize=24" in vf and "Outline=2" in vf and "PrimaryColour=&HFFFFFF&" in vf


class TestPreview:
    def test_preview_reflects_the_style_and_escapes_text(self):
        html_out = sf.style_preview_html("<script>x</script>", sf.ASS_PRESETS["Streamer clip"], "#FF0000")
        assert "<script>" not in html_out and "&lt;script&gt;" in html_out
        assert "font-weight:bold" in html_out and "color:#FF0000" in html_out
        assert sf.style_preview_html("x", sf.ASS_PRESETS["Clean"]) != sf.style_preview_html(
            "x", sf.ASS_PRESETS["Streamer clip"])

    def test_font_and_colour_can_not_inject_css(self):
        style = dict(sf.ASS_PRESETS["Clean"], font="Arial'; } body { display:none",
                     primary="red;background:url(x)")
        out = sf.style_preview_html("x", style)
        assert "display:none" not in out and "url(" not in out


class TestLinesForClip:
    """Step 6e: vertical/shorts export trims the VIDEO to [start, end), so
    the burned subtitles need the same window, timeshifted to start at 0 --
    otherwise a subtitle at absolute time 10:30 would never appear (or
    appear at the wrong moment) in a clip that now starts at 0:00."""

    def _lines(self):
        return [Line(idx=0, start=0.0, end=2.0, zh="A", en="A"),
                Line(idx=1, start=10.0, end=12.0, zh="B", en="B"),
                Line(idx=2, start=20.0, end=22.0, zh="C", en="C")]

    def test_only_overlapping_lines_are_kept(self):
        out = sf.lines_for_clip(self._lines(), 5.0, 15.0)
        assert [l.en for l in out] == ["B"]

    def test_kept_lines_are_timeshifted_to_start_at_zero(self):
        out = sf.lines_for_clip(self._lines(), 10.0, 22.0)
        assert (out[0].start, out[0].end) == (0.0, 2.0)
        assert (out[1].start, out[1].end) == (10.0, 12.0)

    def test_a_partially_overlapping_line_is_clamped_to_the_window(self):
        lines = [Line(idx=0, start=8.0, end=13.0, zh="X", en="X")]
        out = sf.lines_for_clip(lines, 10.0, 20.0)
        assert (out[0].start, out[0].end) == (0.0, 3.0)  # clamped at the clip's own start

    def test_idx_is_renumbered_and_original_lines_untouched(self):
        lines = self._lines()
        out = sf.lines_for_clip(lines, 10.0, 22.0)
        assert [l.idx for l in out] == [0, 1]
        assert lines[1].start == 10.0  # the source line is a copy, not mutated
