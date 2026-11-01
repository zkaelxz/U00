"""
tests/test_export_formats.py -- Step 6b: VTT and ASS export with real
styling, long-line wrapping, overlap clamping, reading-speed checks, and
hardsub styling (ASS burned directly, no force_style).
"""

import pytest

import subtitle_formats as sf
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
        # ">" is escaped, so the arrow can't end the cue and still reads "a --> b".
        assert "a --&gt; b\n你好" in vtt

    @staticmethod
    def _cue_bodies(vtt):
        """Each cue's text as a WebVTT parser reads it: the lines after the
        timing line, up to the first blank line."""
        bodies = []
        for block in vtt.split("\n\n")[1:]:
            rows = block.split("\n")
            timing = next(i for i, r in enumerate(rows) if " --> " in r)
            bodies.append("\n".join(rows[timing + 1:]).rstrip("\n"))
        return bodies

    def test_markup_characters_are_escaped_not_lost(self):
        import html
        lines = _lines()
        lines[0].en = "I <3 you & him > her"
        body = self._cue_bodies(sf.lines_to_vtt(lines, "en"))[0]
        assert body == "I &lt;3 you &amp; him &gt; her"
        assert html.unescape(body) == "I <3 you & him > her"

    def test_every_arrow_is_neutralised(self):
        import html
        lines = _lines()
        lines[0].en = "a ---> b ----> c --> d"
        vtt = sf.lines_to_vtt(lines, "en")
        cue_lines = vtt.split("\n")
        assert sum("-->" in row for row in cue_lines) == 2   # only the two timing lines
        body = self._cue_bodies(vtt)[0]
        assert html.unescape(body) == "a ---> b ----> c --> d"

    def test_blank_lines_inside_a_cue_are_collapsed(self):
        lines = _lines()
        lines[0].en = "first\n\n\nsecond\n \nthird"
        vtt = sf.lines_to_vtt(lines, "en")
        assert self._cue_bodies(vtt) == ["first\nsecond\nthird", "Goodbye."]
        lines = _lines()
        lines[0].en = "only English\n\n"
        lines[0].zh = "\n你好"
        assert self._cue_bodies(sf.lines_to_vtt(lines, "bilingual"))[0] == "only English\n你好"

    def test_sfx_italics_survive_escaping(self):
        lines = [Line(idx=0, start=0.0, end=1.0, zh="砰", en="glass <shatters> & falls", sfx=True)]
        body = self._cue_bodies(sf.lines_to_vtt(lines, "en"))[0]
        assert body == "<i>[glass &lt;shatters&gt; &amp; falls]</i>"
        bi = self._cue_bodies(sf.lines_to_vtt(lines, "bilingual"))[0]
        assert bi == "<i>[glass &lt;shatters&gt; &amp; falls]</i>\n<i>[砰]</i>"

    def test_ordinary_text_is_unchanged(self):
        vtt = sf.lines_to_vtt(_lines(), "bilingual")
        assert self._cue_bodies(vtt) == ["Hello there.\n你好", "Goodbye.\n再见"]
        notes = {0: [{"term": "Qijutang", "note": "lit. 'Hall of Sitting Together'"}]}
        body = self._cue_bodies(sf.lines_to_vtt(_lines(), "en", notes_by_idx=notes))[0]
        assert body == "Hello there.\n[Qijutang: lit. 'Hall of Sitting Together']"

    def test_a_bare_carriage_return_does_not_end_the_cue(self):
        lines = _lines()
        lines[0].en = "one\r\rtwo\rthree"
        body = self._cue_bodies(sf.lines_to_vtt(lines, "en"))[0]
        assert body == "one\ntwo\nthree"

    def test_srt_and_ass_are_not_entity_escaped(self):
        lines = _lines()
        lines[0].en = "I <3 you & --> her"
        assert "\nI <3 you & --> her\n" in lines_to_srt(lines, "en")
        assert "\nI <3 you & --> her\n" in lines_to_bilingual_srt(lines)
        ass = sf.lines_to_ass(lines, sf.ASS_PRESETS["Clean"], "en")
        assert "I <3 you & --> her" in ass and "&lt;" not in ass and "&amp;" not in ass


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
        assert "a \\{\\b1\\} trick\\Nsecond line" in ass

    def test_a_backslash_is_drawn_once_and_never_starts_a_break(self):
        # libass has no backslash escape: a doubled one is drawn twice.
        lines = _lines()
        lines[0].en = "\u00af\\_(\u30c4)_/\u00af C:\\new \\h \\"
        ass = sf.lines_to_ass(lines, sf.ASS_PRESETS["Clean"])
        dialogue = next(l for l in ass.splitlines() if l.startswith("Dialogue:"))
        assert "\u00af\\_(\u30c4)_/\u00af" in dialogue
        assert "C:\\\u2060new \\\u2060h \\" in dialogue
        assert "\\\\" not in dialogue

    def test_a_backslash_survives_a_round_trip_through_import(self):
        import subtitle_parse
        lines = _lines()
        lines[0].en = "C:\\new and \\h"
        cues = subtitle_parse.parse_ass(sf.lines_to_ass(lines, sf.ASS_PRESETS["Clean"]))
        assert cues[0].text == "C:\\new and \\h"

    def test_line_breaks_other_than_newline_cannot_start_a_new_dialogue_line(self):
        lines = _lines()
        lines[0].en = "a\r\nb\rc\u2028d\u2029e\x85f"
        ass = sf.lines_to_ass(lines, sf.ASS_PRESETS["Clean"])
        assert "a\\Nb\\Nc\\Nd\\Ne\\Nf" in ass
        assert len(ass.splitlines()) == len(sf.lines_to_ass(_lines(), sf.ASS_PRESETS["Clean"]).splitlines())

    def test_other_control_characters_become_spaces(self):
        lines = _lines()
        lines[0].en = "a\x0bb\x0cc\x1cd\x1ee\x00f\x7fg\th"
        ass = sf.lines_to_ass(lines, sf.ASS_PRESETS["Clean"])
        assert "a b c d e f g\th" in ass
        assert not any(ch in ass for ch in "\x0b\x0c\x1c\x1d\x1e\x00\x7f")

    LONG_ZH = "他说：「这件事我们明天再谈吧，今天太晚了。」然后转身离开了房间。"

    def _rows(self, ass):
        dialogue = next(l for l in ass.splitlines() if l.startswith("Dialogue:"))
        return dialogue.split(",", 9)[9].split("\\N")

    @pytest.mark.parametrize("preset", ["Clean", "Streamer clip"])
    def test_long_cjk_rows_are_broken_to_fit_the_frame(self, preset):
        # libass only breaks at spaces, so an unbroken sentence ran off both edges.
        style = sf.ASS_PRESETS[preset]
        limit = sf.ass_cjk_row_limit(style)
        lines = [Line(idx=0, start=0, end=5, zh=self.LONG_ZH, en="He said so.")]
        for field in ("zh", "bilingual"):
            rows = self._rows(sf.lines_to_ass(lines, style, field))
            zh_rows = rows[1:] if field == "bilingual" else rows
            assert "".join(zh_rows) == self.LONG_ZH
            assert len(zh_rows) > 1 and all(len(r) <= limit for r in zh_rows)
        assert self._rows(sf.lines_to_ass(lines, style, "bilingual"))[0] == "He said so."

    def test_row_limit_follows_the_size_and_outline(self):
        clean = sf.ass_cjk_row_limit(sf.ASS_PRESETS["Clean"])
        assert clean == 15
        assert sf.ass_cjk_row_limit(dict(sf.ASS_PRESETS["Clean"], size=60)) < clean
        assert sf.ass_cjk_row_limit(dict(sf.ASS_PRESETS["Clean"], outline_width=10)) < clean

    def test_a_tighter_wrap_wins_and_latin_and_short_rows_are_left_to_libass(self):
        lines = [Line(idx=0, start=0, end=5, zh=self.LONG_ZH, en="word " * 30)]
        tight = self._rows(sf.lines_to_ass(lines, sf.ASS_PRESETS["Clean"], "zh", wrap_chars={"zh": 8}))
        assert all(len(r) <= 8 for r in tight)
        assert self._rows(sf.lines_to_ass(lines, sf.ASS_PRESETS["Clean"], "en")) == ["word " * 30]
        lines[0].zh = "你好"
        assert self._rows(sf.lines_to_ass(lines, sf.ASS_PRESETS["Clean"], "zh")) == ["你好"]

    def test_a_long_cjk_note_on_its_own_line_is_broken_too(self):
        lines = [Line(idx=0, start=0, end=5, zh="你好", en="Hi")]
        notes = {0: [{"term": "说法", "note": self.LONG_ZH}]}
        ass = sf.lines_to_ass(lines, sf.ASS_PRESETS["Clean"], "en", notes,
                              notes_as_separate_line=True)
        note = next(l for l in ass.splitlines() if ",Notes," in l).split(",", 9)[9]
        # The note is drawn at 70% size, so more of it fits on a row.
        note_limit = sf.ass_cjk_row_limit(dict(sf.ASS_PRESETS["Clean"], size=17))
        assert note_limit > sf.ass_cjk_row_limit(sf.ASS_PRESETS["Clean"])
        rows = note.split("\\N")
        assert len(rows) > 1 and all(len(r) <= note_limit for r in rows)

    def test_portrait_video_gets_shorter_rows(self):
        clean = sf.ASS_PRESETS["Clean"]
        portrait = sf.ass_cjk_row_limit(clean, 9 / 16)
        assert portrait < sf.ass_cjk_row_limit(clean) == sf.ass_cjk_row_limit(clean, 16 / 9)
        assert sf.ass_cjk_row_limit(clean, float("nan")) == sf.ass_cjk_row_limit(clean)
        lines = [Line(idx=0, start=0, end=5, zh=self.LONG_ZH, en="")]
        rows = self._rows(sf.lines_to_ass(lines, clean, "zh", frame_aspect=9 / 16))
        assert "".join(rows) == self.LONG_ZH and all(len(r) <= portrait for r in rows)

    def test_portrait_rows_use_the_margins_libass_draws(self):
        # libass stretches the side margins to the frame's width but sizes
        # glyphs by its height. Rendered with libass 0.17 on 1080x1920, six
        # full-width glyphs at the Clean size fit inside the margins and seven
        # run off the picture; counting the margins at glyph scale gave five.
        clean = sf.ASS_PRESETS["Clean"]
        assert sf.ass_cjk_row_limit(clean, 9 / 16) == 6
        assert sf.ass_cjk_row_limit(clean, 3 / 4) == 8
        assert sf.ass_cjk_row_limit(sf.ASS_PRESETS["Streamer clip"], 9 / 16) == 4
        assert sf.ass_cjk_row_limit(clean, 4 / 3) == 15

    def test_presets_differ_and_clean_is_the_old_hardsub_look(self):
        assert sf.ASS_PRESETS["Clean"]["size"] == 24 and sf.ASS_PRESETS["Clean"]["outline_width"] == 2
        assert sf.ASS_PRESETS["Streamer clip"]["bold"] is True
        assert sf.ASS_PRESETS["Streamer clip"]["outline_width"] > sf.ASS_PRESETS["Clean"]["outline_width"]

    def test_speaker_colours_are_distinct_by_default(self):
        colors = sf.default_speaker_colors(["A", "B", "C", None])
        assert len(set(colors.values())) == 3 and None not in colors


class TestAssShadow:
    """Step 6i: _style_line used to hardcode ASS's own Shadow field to
    the literal "1", ignoring anything in the style dict -- a shadow
    slider now threads a real value through instead."""

    def test_shadow_value_reaches_the_style_line(self):
        style = dict(sf.ASS_PRESETS["Clean"], shadow=4)
        ass = sf.lines_to_ass(_lines(), style)
        default_style = [l for l in ass.splitlines() if l.startswith("Style: Default,")][0]
        assert default_style.split(",")[17] == "4"  # Shadow field

    def test_missing_shadow_key_falls_back_to_1(self):
        style = {k: v for k, v in sf.ASS_PRESETS["Clean"].items() if k != "shadow"}
        ass = sf.lines_to_ass(_lines(), style)
        default_style = [l for l in ass.splitlines() if l.startswith("Style: Default,")][0]
        assert default_style.split(",")[17] == "1"

    def test_both_presets_default_to_shadow_1(self):
        assert sf.ASS_PRESETS["Clean"]["shadow"] == 1
        assert sf.ASS_PRESETS["Streamer clip"]["shadow"] == 1


class TestAssNotesAsSeparateLine:
    """Step 6i: an opt-in toggle so a translation note renders as its own
    smaller second Dialogue line instead of being appended inline to the
    same cue -- off by default (today's behavior), and SRT/VTT have no
    such toggle at all (they always append inline)."""

    def _lines_with_note(self):
        lines = _lines()
        notes_by_idx = {0: [{"term": "Qijutang", "note": "lit. 'Hall of Sitting Together'"}]}
        return lines, notes_by_idx

    def test_off_by_default_keeps_the_inline_suffix(self):
        lines, notes_by_idx = self._lines_with_note()
        ass = sf.lines_to_ass(lines, sf.ASS_PRESETS["Clean"], notes_by_idx=notes_by_idx)
        dialogue = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
        assert len(dialogue) == 2  # no extra note line
        assert "Qijutang" in dialogue[0]

    def test_on_produces_a_second_smaller_dialogue_line(self):
        lines, notes_by_idx = self._lines_with_note()
        style = sf.ASS_PRESETS["Clean"]
        ass = sf.lines_to_ass(lines, style, notes_by_idx=notes_by_idx, notes_as_separate_line=True)
        dialogue = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
        assert len(dialogue) == 3  # line 0's main + its note, then line 1's main
        assert "Qijutang" not in dialogue[0]  # main cue stays clean
        assert dialogue[1].split(",")[3] == "Notes"
        assert "Qijutang" in dialogue[1]
        note_style = [l for l in ass.splitlines() if l.startswith("Style: Notes,")][0]
        assert note_style.split(",")[2] == str(round(style["size"] * 0.7))  # Fontsize

    def test_a_line_with_no_note_gets_no_extra_dialogue_line(self):
        lines, notes_by_idx = self._lines_with_note()
        ass = sf.lines_to_ass(lines, sf.ASS_PRESETS["Clean"], notes_by_idx=notes_by_idx,
                              notes_as_separate_line=True)
        dialogue = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
        assert len(dialogue) == 3  # line 0's note, but nothing extra for line 1

    def test_srt_and_vtt_have_no_such_toggle_and_always_stay_inline(self):
        lines, notes_by_idx = self._lines_with_note()
        srt = lines_to_srt(lines, "en", notes_by_idx=notes_by_idx)
        vtt = sf.lines_to_vtt(lines, "en", notes_by_idx=notes_by_idx)
        assert "Qijutang" in srt and "Qijutang" in vtt


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


class TestReadingSpeed:
    def test_too_dense_is_flagged_normal_is_not(self):
        dense = Line(idx=0, start=0, end=2.0, zh="a", en="This translation is far too long to read in two seconds.")
        normal = Line(idx=1, start=1, end=4.0, zh="b", en="Nice to meet you.")
        assert [ln.idx for ln, _, _ in sf.dense_lines([dense, normal])] == [0]
        assert sf.flag_dense_lines([dense, normal]) == 1
        assert dense.flag == sf.READING_SPEED_FLAG and normal.flag is None

    def test_limits_follow_the_script(self):
        assert sf.script_of("你好世界") == "cjk" and sf.script_of("Hello") == "latin"
        assert sf.SCRIPT_CPS["cjk"] < sf.SCRIPT_CPS["latin"]
        # 15 CJK characters in 1.5s is 10/s: fine for English, too dense for CJK
        cjk = Line(idx=0, start=0, end=1.5, zh="", en="我们一起去公园散步吧好吗啊啦呀")
        assert sf.dense_lines([cjk])

    def test_an_existing_flag_is_never_replaced(self):
        ln = Line(idx=0, start=0, end=2, zh="a", en="Way too much text for a single second.",
                  flag="uncertain_translation")
        assert sf.flag_dense_lines([ln]) == 0 and ln.flag == "uncertain_translation"
    @staticmethod
    def _en(text, seconds):
        return Line(idx=0, start=0, end=seconds, zh="", en=text)

    def test_a_cue_shorter_than_the_duration_floor_is_never_flagged(self):
        text = "x" * 40  # 40 chars in 1.4 s is far over the limit
        assert not sf.dense_lines([self._en(text, sf.MIN_DENSE_DURATION_S - 0.1)])
        assert sf.dense_lines([self._en(text, sf.MIN_DENSE_DURATION_S)])  # exactly at the floor counts

    def test_each_mode_uses_its_own_limits(self):
        text = "word " * 20          # 99 chars after strip
        ln = self._en(text, 5.0)     # 19.8 cps: over normal (17), under relaxed (20)
        assert sf.dense_lines([ln], mode="normal")
        assert not sf.dense_lines([ln], mode="relaxed")
        assert not sf.dense_lines([ln], mode="off")
        fast = self._en(text, 3.0)   # 33 cps: over relaxed too
        assert sf.dense_lines([fast], mode="relaxed")[0][2] == sf.cps_limit("latin", "relaxed")
        assert not sf.dense_lines([fast], mode="off")

    def test_off_flags_nothing_and_an_unknown_stored_mode_is_normal(self):
        ln = self._en("word " * 60, 3.0)
        assert sf.flag_dense_lines([ln], mode="off") == 0 and ln.flag is None
        assert sf.reading_speed_mode_of({"reading_speed_mode": "bogus"}) == "normal"
        assert sf.reading_speed_mode_of({"reading_speed_mode": None}) == "normal"
        assert sf.reading_speed_mode_of({"reading_speed_mode": "relaxed"}) == "relaxed"

    def test_the_note_says_what_the_speech_allows(self):
        ln = self._en("word " * 60, 3.0)
        sf.flag_dense_lines([ln])
        assert "The speech is this fast: shorten the text" in ln.flag_note

    def test_translation_finishing_honours_the_titles_mode(self, isolated_db):
        import translation_finish
        text = "word " * 20  # 19.8 cps over 5 s
        for mode, expected in (("normal", sf.READING_SPEED_FLAG), ("relaxed", None), ("off", None)):
            did = isolated_db.create_drama(title_en=mode)
            isolated_db.update_drama(did, reading_speed_mode=mode)
            isolated_db.save_lines(did, [Line(idx=0, start=0, end=5, zh="你好", en=text)])
            lines = isolated_db.load_line_objects(did)
            translation_finish.finish_translation_run(did, lines, object(), "claude", "", [], [],
                                                  enforce_ids={ln.id for ln in lines})
            assert isolated_db.load_lines(did)[0]["flag"] == expected, mode

    def test_translate_job_puts_dense_lines_in_the_review_queue(self, isolated_db, monkeypatch):
        from jobs import job_table
        from engine_backends import translate_pipeline
        from services.workspace_job_service import run_translate_job
        did = isolated_db.create_drama(title_en="D")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=2, zh="你好"),
                                     Line(idx=1, start=2, end=6, zh="再见")])
        lines = isolated_db.load_line_objects(did)

        def fake_translate(lines, engine, **kw):
            lines[0].en = "An extremely long translation that nobody could read in a second."
            lines[1].en = "Bye."
            kw["save_cb"](lines)
            return lines, []
        monkeypatch.setattr(translate_pipeline, "translate_lines_with_engine", fake_translate)
        job_id = "test_cps_translate"
        job_table._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                          "error": None, "cancel_requested": False, "result": None}
        run_translate_job(job_id, did, lines, object(), {"id": did}, "", None, False, "en-US",
                          None, "", "claude", "audio_drama")
        job_table._jobs.pop(job_id, None)
        rows = isolated_db.load_lines(did)
        assert rows[0]["flag"] == sf.READING_SPEED_FLAG and rows[1]["flag"] is None


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


class TestCjkWordAwareWrap:
    """wrap_text breaks CJK between words when a segmenter offers offsets."""

    TEXT = "あいうえおかきくけこさしすせそたちつてと"  # 20 chars, no punctuation

    @staticmethod
    def _tiers(*tiers):
        return lambda text, language, script="simplified": [set(t) for t in tiers]

    def test_cut_is_the_last_allowed_offset_in_the_window(self, monkeypatch):
        import segment
        monkeypatch.setattr(segment, "line_break_tiers", self._tiers({4, 12, 15}))
        assert sf.wrap_text(self.TEXT, 16, "ja").split("\n")[0] == self.TEXT[:15]

    def test_first_tier_wins_over_a_later_offset_in_a_lower_tier(self, monkeypatch):
        import segment
        monkeypatch.setattr(segment, "line_break_tiers", self._tiers({10}, {14}))
        assert sf.wrap_text(self.TEXT, 16, "ja").split("\n")[0] == self.TEXT[:10]

    def test_no_allowed_offset_in_the_window_cuts_at_the_limit(self, monkeypatch):
        import segment
        monkeypatch.setattr(segment, "line_break_tiers", self._tiers({2}))  # below the floor
        assert sf.wrap_text(self.TEXT, 16, "ja").split("\n")[0] == self.TEXT[:16]

    def test_language_not_given_keeps_todays_behaviour(self, monkeypatch):
        import segment
        monkeypatch.setattr(segment, "line_break_tiers", self._tiers({10}))
        assert sf.wrap_text(self.TEXT, 16).split("\n")[0] == self.TEXT[:16]

    def test_segmenter_not_installed_keeps_todays_behaviour(self, monkeypatch):
        import segment

        def missing(*_a, **_k):
            raise ImportError
        monkeypatch.setattr(segment, "line_break_tiers", missing)
        assert sf.wrap_text(self.TEXT, 16, "ja").split("\n")[0] == self.TEXT[:16]
        monkeypatch.setattr(segment, "line_break_tiers", lambda *a, **k: None)
        assert sf.wrap_text(self.TEXT, 16, "ja").split("\n")[0] == self.TEXT[:16]

    def test_fallback_cut_never_leaves_a_lone_full_stop_or_small_kana(self):
        # 17 characters: a cut at 16 would leave "。" alone on line two.
        assert sf.wrap_text("あいうえおかきくけこさしすせそた。", 16) == "あいうえおかきくけこさしすせそ\nた。"
        assert sf.wrap_text("あいうえおかきくけこさしすせそしゃ", 16) == "あいうえおかきくけこさしすせそ\nしゃ"

    @pytest.mark.parametrize("text", [
        "彼は何も食べないでそのまま寝てしまったので、朝になってもまだ眠かったのです。そうですね",
        "あいうえおかきくけこさしすせそたちつてとなにぬねのっぺぺゃゅょ、。ー",
    ])
    def test_no_cut_lands_before_punctuation_or_small_kana(self, text, monkeypatch):
        import segment
        monkeypatch.setattr(segment, "line_break_tiers",
                            lambda t, lang, script="simplified": [set(range(1, len(t)))])
        lines = sf.wrap_text(text, 16, "ja").split("\n")
        assert "".join(lines) == text.replace("\n", "")
        assert all(ln and ln[0] not in sf.NO_LINE_START for ln in lines)

    def test_language_reaches_every_format(self, monkeypatch):
        import segment
        calls = []

        def tiers(text, language, script="simplified"):
            calls.append((language, script))
            return [{10}]
        monkeypatch.setattr(segment, "line_break_tiers", tiers)
        lines = [Line(idx=0, start=0, end=5, zh=self.TEXT, en="Hi")]
        wrap = {"en": 42, "zh": 16}
        kw = {"language": "ja", "chinese_script": "traditional"}
        first = self.TEXT[:10]
        assert first + "\nさし" in sf.lines_to_vtt(lines, "zh", wrap_chars=wrap, **kw)
        assert first + " " in sf.lines_to_lrc(lines, "zh", wrap_chars=wrap, **kw)
        assert first + "\\N" in sf.lines_to_ass(lines, sf.ASS_PRESETS["Clean"], "zh", wrap_chars=wrap, **kw)
        assert sf.wrap_lines(lines, wrap, **kw)[0].zh.split("\n")[0] == first
        assert set(calls) == {("ja", "traditional")}

    def test_translation_text_is_not_broken_with_the_source_language(self, monkeypatch):
        import segment
        monkeypatch.setattr(segment, "line_break_tiers", self._tiers({10}))
        lines = [Line(idx=0, start=0, end=5, zh="x", en=self.TEXT)]
        out = sf.lines_to_vtt(lines, "en", wrap_chars={"en": 16, "zh": 16}, language="ja")
        assert self.TEXT[:16] + "\n" in out


class TestSrtFileText:
    def test_non_ascii_text_gets_one_bom(self):
        assert sf.srt_file_text("1\n你好\n") == "\ufeff1\n你好\n"
        assert sf.srt_file_text("\ufeff1\n你好\n") == "\ufeff1\n你好\n"
        assert sf.srt_file_text("1\ncafé\n").startswith("\ufeff")

    def test_ascii_text_is_unchanged(self):
        assert sf.srt_file_text("1\nhi\n") == "1\nhi\n"
        assert sf.srt_file_text("") == ""
