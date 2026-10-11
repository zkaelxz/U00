"""Per-language reading-speed limits, the flag note that says whether a cue
could be shown longer, and the opt-in "Tidy cue timing for players" pass for
SRT exports (ASS, VTT and LRC are deliberately left alone)."""
import copy
import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")  # TestClient's transport
from fastapi.testclient import TestClient

import subtitle_formats as sf
from api.api_config import ApiSettings
from api.server import create_app
from core import Line, fmt_ts, lines_to_srt
from services import export_service

FPS = 24.0
GAP = 2 / FPS
MIN_DUR = 20 / FPS


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                      headers={"X-Baihe-Local": "1"})


def _line(idx, start, end, text="x", **kw):
    return Line(idx=idx, start=start, end=end, zh="源", en=text, **kw)


class TestReadingSpeedProfiles:
    def test_table_holds_the_netflix_values(self):
        assert sf.CPS_BY_LANGUAGE == {"en": 17.0, "zh": 9.0, "ja": 4.0, "ko": 12.0}

    def test_english_normal_is_17_and_relaxed_is_netflix_max_20(self):
        at_limit = _line(0, 0, 3, "a" * 51)       # 17.0 cps
        over = _line(0, 0, 3, "a" * 54)           # 18.0 cps
        way_over = _line(0, 0, 3, "a" * 63)       # 21.0 cps
        assert not sf.dense_lines([at_limit])
        assert sf.dense_lines([over], mode="normal")
        assert not sf.dense_lines([over], mode="relaxed")
        assert sf.dense_lines([way_over], mode="relaxed")
        assert sf.dense_lines([way_over], mode="relaxed")[0][2] == pytest.approx(20.0)

    def test_streamer_mode_is_looser_than_relaxed(self):
        ln = _line(0, 0, 3, "a" * 66)             # 22 cps
        assert sf.dense_lines([ln], mode="relaxed")
        assert not sf.dense_lines([ln], mode=sf.STREAMER_MODE)

    @pytest.mark.parametrize("language,chars,flagged", [
        ("zh", 27, False), ("zh", 30, True),      # 9 cps over 3 s
        ("ja", 12, False), ("ja", 15, True),      # 4 cps
        ("ko", 36, False), ("ko", 39, True),      # 12 cps
    ])
    def test_source_language_limits(self, language, chars, flagged):
        ln = Line(idx=0, start=0, end=3, zh="あ" * chars, en="")
        assert bool(sf.dense_lines([ln], field="zh", language=language)) is flagged

    def test_japanese_is_stricter_than_chinese_for_the_same_text(self):
        ln = Line(idx=0, start=0, end=3, zh="あ" * 20, en="")   # 6.7 cps
        assert sf.dense_lines([ln], field="zh", language="ja")
        assert not sf.dense_lines([ln], field="zh", language="zh")

    def test_cjk_text_without_a_language_uses_the_chinese_value(self):
        ln = _line(0, 0, 3, "我" * 30)            # 10 cps
        assert sf.dense_lines([ln])[0][2] == 9.0

    def test_every_relaxed_limit_is_above_its_normal_limit(self):
        for key, normal in {**sf.CPS_BY_LANGUAGE, **sf.SCRIPT_CPS}.items():
            assert sf.cps_limit(key, "relaxed") > normal

    def test_character_floor_still_protects_short_japanese_cues(self):
        short = Line(idx=0, start=0, end=1.5, zh="あ" * (sf.MIN_DENSE_CHARS - 1), en="")
        assert not sf.dense_lines([short], field="zh", language="ja")
        at_floor = Line(idx=0, start=0, end=1.5, zh="あ" * sf.MIN_DENSE_CHARS, en="")
        assert sf.dense_lines([at_floor], field="zh", language="ja")

    def test_an_existing_flag_is_not_replaced_so_a_dismissed_one_stays_cleared(self):
        ln = _line(0, 0, 2, "a" * 80, flag="uncertain_translation")
        assert sf.flag_dense_lines([ln]) == 0 and ln.flag == "uncertain_translation"


class TestDenseFlagNote:
    """A reading-speed flag only says what is true; it never moves a time."""

    def _flagged(self, lines):
        before = [(ln.start, ln.end) for ln in lines]
        assert sf.flag_dense_lines(lines) >= 1
        assert [(ln.start, ln.end) for ln in lines] == before
        return lines[0].flag_note

    def test_free_time_after_the_cue_is_reported(self):
        # 63 chars in 3 s is 21 cps; 17 cps needs 3.71 s, so 0.71 s more.
        lines = [_line(0, 0, 3, "a" * 63), _line(1, 10, 12)]
        note = self._flagged(lines)
        assert "could be shown 0.8 s longer" in note and "speech is this fast" not in note

    def test_back_to_back_speech_means_shorten_the_text(self):
        note = self._flagged([_line(0, 0, 3, "a" * 63), _line(1, 3, 6)])
        assert "The speech is this fast: shorten the text" in note and "longer" not in note

    def test_room_that_is_too_small_is_not_offered(self):
        # needs 0.71 s but only 0.3 s remains after the 2-frame gap
        note = self._flagged([_line(0, 0, 3, "a" * 63), _line(1, 3 + 0.3 + GAP, 6)])
        assert "The speech is this fast" in note

    def test_the_last_cue_has_no_later_speech_to_collide_with(self):
        assert "could be shown" in self._flagged([_line(0, 0, 3, "a" * 63)])

    def test_the_note_offers_no_timing_fix_when_there_is_no_room(self):
        note = self._flagged([_line(0, 0, 3, "a" * 63), _line(1, 3, 6)])
        assert "lengthen" not in note and "stretch" not in note.lower()


class TestTidyCueTiming:
    def test_a_short_cue_is_extended_into_free_silence(self):
        out, rep = sf.tidy_cue_timing([_line(0, 0, 0.3), _line(1, 5, 6)], FPS)
        assert out[0].end == pytest.approx(MIN_DUR) and rep["extended"] == 1

    def test_extension_stops_a_gap_before_the_next_cue(self):
        out, _ = sf.tidy_cue_timing([_line(0, 0, 0.3), _line(1, 0.6, 3)], FPS)
        assert out[0].end == pytest.approx(0.6 - GAP)

    def test_extension_is_at_most_one_second(self):
        out, _ = sf.tidy_cue_timing([_line(0, 0, 0.05), _line(1, 5, 6)], 8.0)  # 20 frames = 2.5 s
        assert out[0].end == pytest.approx(1.05)

    def test_starts_never_move_and_originals_are_left_alone(self):
        lines = [_line(0, 0, 0.3), _line(1, 0.6, 3)]
        out, _ = sf.tidy_cue_timing(lines, FPS)
        assert [ln.end for ln in lines] == [0.3, 3] and [c.start for c in out] == [0, 0.6]

    def test_touching_cues_lose_two_frames_from_the_earlier_one(self):
        out, rep = sf.tidy_cue_timing([_line(0, 0, 2), _line(1, 2, 4)], FPS)
        assert out[0].end == pytest.approx(2 - GAP) and out[1].end == 4 and rep["trimmed"] == 1

    def test_overlapping_cues_are_trimmed_by_at_most_two_frames(self):
        clamped, _ = sf.clamp_overlaps([_line(0, 0, 3.4), _line(1, 3, 5)])
        out, _ = sf.tidy_cue_timing(clamped, FPS)
        assert out[0].end == pytest.approx(3 - GAP)

    def test_a_cue_too_short_to_trim_is_left_alone(self):
        out, rep = sf.tidy_cue_timing([_line(0, 0, 0.05), _line(1, 0.05, 3)], FPS)
        assert out[0].end == 0.05 and rep == {"extended": 0, "trimmed": 0, "left_long": []}

    def test_long_cues_are_reported_and_never_cut(self):
        out, rep = sf.tidy_cue_timing([_line(0, 0, 16), _line(1, 20, 21), _line(2, 22, 30)], FPS)
        assert [c.end for c in out] == [16, 21, 30]
        assert rep["left_long"] == [1, 3]  # cue numbers as in the SRT

    def test_nothing_is_changed_when_there_is_nothing_to_tidy(self):
        lines = [_line(0, 0, 2), _line(1, 3, 5)]
        out, rep = sf.tidy_cue_timing(lines, FPS)
        assert [(c.start, c.end) for c in out] == [(0, 2), (3, 5)]
        assert rep == {"extended": 0, "trimmed": 0, "left_long": []}

    @pytest.mark.parametrize("fps", [None, 0, -5, float("nan")])
    def test_unknown_frame_rate_means_24(self, fps):
        out, _ = sf.tidy_cue_timing([_line(0, 0, 0.3)], fps)
        assert out[0].end == pytest.approx(20 / 24)

    def test_frame_rate_scales_both_rules(self):
        out, _ = sf.tidy_cue_timing([_line(0, 0, 0.3), _line(1, 10, 11), _line(2, 11, 12)], 25.0)
        assert out[0].end == pytest.approx(0.8) and out[1].end == pytest.approx(11 - 2 / 25)

    def test_sfx_cues_follow_the_same_rules(self):
        out, _ = sf.tidy_cue_timing([_line(0, 0, 0.2, sfx=True)], FPS)
        assert out[0].end == pytest.approx(MIN_DUR)


def _seed(db, rows):
    did = db.create_drama(title_en="T")
    db.save_lines(did, rows)
    return did


ROWS = [Line(idx=0, start=0.0, end=0.3, zh="甲", en="Hi"),
        Line(idx=1, start=2.0, end=4.0, zh="乙", en="Yes"),
        Line(idx=2, start=4.0, end=20.0, zh="丙", en="Long one")]


class TestExportOption:
    def test_off_is_byte_identical_to_the_untidied_export(self, isolated_db):
        did = _seed(isolated_db, ROWS)
        clamped, _ = sf.clamp_overlaps(core_lines(isolated_db, did))
        expected = lines_to_srt(clamped, "en")
        result = export_service.generate_subtitle_export(did, "srt", "en")
        assert result["text"] == expected
        assert (result["cues_extended"], result["cues_trimmed"], result["cues_left_long"]) == (0, 0, [])

    def test_on_tidies_srt_and_reports_counts(self, isolated_db):
        did = _seed(isolated_db, ROWS)
        result = export_service.generate_subtitle_export(did, "srt", "en", tidy_timing=True)
        cues = result["text"].split("\n\n")
        assert fmt_ts(MIN_DUR) in cues[0]
        assert fmt_ts(4.0 - GAP) in cues[1]
        assert fmt_ts(20.0) in cues[2]  # long cue is kept as it is
        assert (result["cues_extended"], result["cues_trimmed"], result["cues_left_long"]) == (1, 1, [3])
        assert [(r["start"], r["end"]) for r in isolated_db.load_lines(did)] == [
            (0.0, 0.3), (2.0, 4.0), (4.0, 20.0)]

    def test_bilingual_srt_is_tidied_too(self, isolated_db):
        did = _seed(isolated_db, ROWS)
        text = export_service.generate_subtitle_export(did, "srt", "bilingual", tidy_timing=True)["text"]
        assert fmt_ts(MIN_DUR) in text

    @pytest.mark.parametrize("fmt", ["vtt", "lrc"])
    def test_vtt_and_lrc_ignore_the_option(self, isolated_db, fmt):
        did = _seed(isolated_db, ROWS)
        plain = export_service.generate_subtitle_export(did, fmt, "en")
        tidy = export_service.generate_subtitle_export(did, fmt, "en", tidy_timing=True)
        assert tidy == plain and tidy["cues_extended"] == 0

    def test_ass_keeps_todays_timing(self, isolated_db):
        did = _seed(isolated_db, ROWS)
        text = export_service.generate_ass_text(did, per_speaker_colors=False)
        assert "0:00:00.00,0:00:00.30" in text and "0:00:04.00,0:00:20.00" in text

    def test_route_option_and_headers(self, client, isolated_db):
        did = _seed(isolated_db, ROWS)
        base = f"/api/export/dramas/{did}/subtitle?fmt=srt&field=en"
        off = client.get(base)
        assert off.headers["X-Cues-Extended"] == "0" and off.headers["X-Cues-Left-Long"] == ""
        on = client.get(base + "&tidy_timing=true")
        assert on.headers["X-Cues-Extended"] == "1" and on.headers["X-Cues-Trimmed"] == "1"
        assert on.headers["X-Cues-Left-Long"] == "3"
        assert on.text != off.text


class TestProbedFrameRate:
    """Tidy uses the title's real frame rate; every failure is the 24 fps default."""

    @staticmethod
    def _title(db, with_video=True):
        did = _seed(db, ROWS)
        if with_video:
            db.update_drama(did, source_video_filename="v.mp4")
            open(os.path.join(db.drama_dir(did), "v.mp4"), "wb").close()
        return did

    @pytest.fixture(autouse=True)
    def _empty_fps_cache(self):
        export_service._fps_cache.clear()
        yield
        export_service._fps_cache.clear()

    @staticmethod
    def _probe(monkeypatch, rate=None, error=None):
        calls = []

        def fake(path, timeout=30):
            assert timeout <= 10
            calls.append(path)
            if error:
                raise error
            return {"format": {}, "streams": [{"codec_type": "video", "r_frame_rate": rate}]}
        monkeypatch.setattr(export_service.media_inspect, "run_ffprobe", fake)
        return calls

    def test_repeat_export_does_not_probe_again(self, isolated_db, monkeypatch):
        calls = self._probe(monkeypatch, "25/1")
        did = self._title(isolated_db)
        for _ in range(3):
            assert export_service.generate_subtitle_export(did, "srt", "en", tidy_timing=True)["fps"] == 25.0
        assert len(calls) == 1

    def test_failed_probe_is_not_remembered(self, isolated_db, monkeypatch):
        """A timeout or a missing ffprobe is transient: it must not pin 24 fps
        for the file once ffprobe works again."""
        calls = self._probe(monkeypatch, error=export_service.media_inspect.ProbeError("slow"))
        did = self._title(isolated_db)
        assert export_service.generate_subtitle_export(did, "srt", "en", tidy_timing=True)["fps"] == 24.0
        assert export_service._fps_cache == {}
        self._probe(monkeypatch, "25/1")
        assert export_service.generate_subtitle_export(did, "srt", "en", tidy_timing=True)["fps"] == 25.0
        assert len(calls) == 1

    def test_changed_media_file_is_probed_again(self, isolated_db, monkeypatch):
        calls = self._probe(monkeypatch, "25/1")
        did = self._title(isolated_db)
        export_service.generate_subtitle_export(did, "srt", "en", tidy_timing=True)
        with open(os.path.join(isolated_db.drama_dir(did), "v.mp4"), "wb") as f:
            f.write(b"new video")
        self._probe(monkeypatch, "30/1")
        assert export_service.generate_subtitle_export(did, "srt", "en", tidy_timing=True)["fps"] == 30.0

    def test_cache_is_bounded(self, isolated_db, monkeypatch):
        self._probe(monkeypatch, "25/1")
        monkeypatch.setattr(export_service, "_FPS_CACHE_MAX", 2)
        dids = [self._title(isolated_db) for _ in range(3)]
        for did in dids:
            export_service.generate_subtitle_export(did, "srt", "en", tidy_timing=True)
        assert len(export_service._fps_cache) == 2

    @pytest.mark.parametrize("rate,fps", [("24000/1001", 23.976), ("25/1", 25.0), ("30/1", 30.0)])
    def test_uses_the_probed_rate(self, isolated_db, monkeypatch, rate, fps):
        self._probe(monkeypatch, rate)
        result = export_service.generate_subtitle_export(self._title(isolated_db), "srt", "en", tidy_timing=True)
        assert result["fps"] == pytest.approx(fps, abs=0.001)
        assert fmt_ts(20 / result["fps"]) in result["text"].split("\n\n")[0]

    def test_no_media_is_24(self, isolated_db, monkeypatch):
        self._probe(monkeypatch, error=AssertionError("must not probe"))
        did = self._title(isolated_db, with_video=False)
        assert export_service.generate_subtitle_export(did, "srt", "en", tidy_timing=True)["fps"] == 24.0

    def test_probe_failure_is_24(self, isolated_db, monkeypatch):
        self._probe(monkeypatch, error=export_service.media_inspect.ProbeError("boom"))
        result = export_service.generate_subtitle_export(self._title(isolated_db), "srt", "en", tidy_timing=True)
        assert result["fps"] == 24.0 and fmt_ts(MIN_DUR) in result["text"]

    def test_unusable_rate_is_24(self, isolated_db, monkeypatch):
        self._probe(monkeypatch, "0/0")
        assert export_service.generate_subtitle_export(
            self._title(isolated_db), "srt", "en", tidy_timing=True)["fps"] == 24.0

    def test_not_probed_unless_tidying(self, isolated_db, monkeypatch):
        self._probe(monkeypatch, error=AssertionError("must not probe"))
        result = export_service.generate_subtitle_export(self._title(isolated_db), "srt", "en")
        assert result["fps"] is None

    def test_header_is_a_bare_number(self, client, isolated_db, monkeypatch):
        self._probe(monkeypatch, "25/1")
        did = self._title(isolated_db)
        resp = client.get(f"/api/export/dramas/{did}/subtitle?fmt=srt&field=en&tidy_timing=true")
        assert resp.headers["X-Cue-Fps"] == "25"
        assert "X-Cue-Fps" not in client.get(f"/api/export/dramas/{did}/subtitle?fmt=srt&field=en").headers


def core_lines(db, did):
    from core import lines_from_rows
    return lines_from_rows(db.load_lines(did))


def test_flagging_a_title_never_changes_stored_times(isolated_db):
    rows = [Line(idx=0, start=0.0, end=3.0, zh="甲", en="a" * 63),
            Line(idx=1, start=3.0, end=6.0, zh="乙", en="ok")]
    did = _seed(isolated_db, rows)
    assert export_service.flag_dense_lines(did) == {"flagged_count": 1}
    stored = isolated_db.load_lines(did)
    assert [(r["start"], r["end"]) for r in stored] == [(0.0, 3.0), (3.0, 6.0)]
    assert "speech is this fast" in stored[0]["flag_note"]
