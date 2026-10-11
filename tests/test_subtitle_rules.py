"""Per-language reading-speed limits and the Netflix-style duration/gap pass
that SRT exports go through (ASS, VTT and LRC are deliberately left alone)."""
import copy

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")  # TestClient's transport
from fastapi.testclient import TestClient

import subtitle_formats as sf
from api.api_config import ApiSettings
from api.server import create_app
from core import Line, fmt_ts
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


class TestTimingRules:
    def test_a_short_cue_is_extended_to_twenty_frames(self):
        out, n = sf.apply_timing_rules([_line(0, 0, 0.3), _line(1, 5, 6)], FPS)
        assert out[0].end == pytest.approx(MIN_DUR) and n == 1

    def test_extension_stops_a_gap_before_the_next_cue(self):
        out, _ = sf.apply_timing_rules([_line(0, 0, 0.3), _line(1, 0.6, 3)], FPS)
        assert out[0].end == pytest.approx(0.6 - GAP)

    def test_a_cue_is_never_shrunk_to_make_room_for_the_gap(self):
        out, n = sf.apply_timing_rules([_line(0, 0, 0.3), _line(1, 0.35, 3)], FPS)
        assert out[0].end == 0.3 and n == 0

    def test_a_long_cue_is_capped_at_seven_seconds(self):
        out, n = sf.apply_timing_rules([_line(0, 0, 16)], FPS)
        assert out[0].end == 7.0 and n == 1

    def test_back_to_back_cues_get_a_two_frame_gap(self):
        out, n = sf.apply_timing_rules([_line(0, 0, 2), _line(1, 2, 4)], FPS)
        assert out[0].end == pytest.approx(2 - GAP) and out[1].end == 4 and n == 1

    def test_overlapping_cues_are_gapped_after_the_clamp(self):
        clamped, _ = sf.clamp_overlaps([_line(0, 0, 3.4), _line(1, 3, 5)])
        out, _ = sf.apply_timing_rules(clamped, FPS)
        assert out[0].end == pytest.approx(3 - GAP)

    def test_a_cue_already_clear_of_the_next_is_untouched(self):
        lines = [_line(0, 0, 2), _line(1, 3, 5)]
        out, n = sf.apply_timing_rules(lines, FPS)
        assert [(c.start, c.end) for c in out] == [(0, 2), (3, 5)] and n == 0

    def test_starts_never_move_and_originals_are_left_alone(self):
        lines = [_line(0, 0, 0.3), _line(1, 0.6, 20)]
        before = copy.deepcopy([(ln.start, ln.end) for ln in lines])
        out, n = sf.apply_timing_rules(lines, FPS)
        assert [(ln.start, ln.end) for ln in lines] == before
        assert [c.start for c in out] == [0, 0.6] and n == 2

    def test_nothing_ends_after_the_next_cues_start_minus_the_gap(self):
        lines = [_line(i, i * 0.5, i * 0.5 + 0.2) for i in range(6)]
        out, _ = sf.apply_timing_rules(lines, FPS)
        for cur, nxt in zip(out, out[1:]):
            assert cur.end <= nxt.start - GAP + 1e-9

    @pytest.mark.parametrize("fps", [None, 0, -5, float("nan")])
    def test_unknown_frame_rate_means_24(self, fps):
        out, _ = sf.apply_timing_rules([_line(0, 0, 0.3)], fps)
        assert out[0].end == pytest.approx(20 / 24)

    def test_frame_rate_scales_both_rules(self):
        out, _ = sf.apply_timing_rules([_line(0, 0, 0.3), _line(1, 10, 11), _line(2, 11, 12)], 25.0)
        assert out[0].end == pytest.approx(0.8)
        assert out[1].end == pytest.approx(11 - 2 / 25)

    def test_sfx_cues_follow_the_same_rules(self):
        out, _ = sf.apply_timing_rules([_line(0, 0, 0.2, sfx=True)], FPS)
        assert out[0].end == pytest.approx(MIN_DUR)


def _seed(db, rows):
    did = db.create_drama(title_en="T")
    db.save_lines(did, rows)
    return did


ROWS = [Line(idx=0, start=0.0, end=0.3, zh="甲", en="Hi"),
        Line(idx=1, start=2.0, end=4.0, zh="乙", en="Yes"),
        Line(idx=2, start=4.0, end=20.0, zh="丙", en="Long one")]


class TestExportAppliesTheRules:
    def test_srt_is_adjusted_and_counted_and_stored_times_are_not(self, isolated_db):
        did = _seed(isolated_db, ROWS)
        result = export_service.generate_subtitle_export(did, "srt", "en")
        cues = result["text"].split("\n\n")
        assert fmt_ts(MIN_DUR) in cues[0]
        assert fmt_ts(4.0 - GAP) in cues[1]
        assert fmt_ts(11.0) in cues[2]
        assert result["timing_adjusted_count"] == 3
        assert [(r["start"], r["end"]) for r in isolated_db.load_lines(did)] == [
            (0.0, 0.3), (2.0, 4.0), (4.0, 20.0)]

    def test_bilingual_srt_gets_the_same_rules(self, isolated_db):
        did = _seed(isolated_db, ROWS)
        assert fmt_ts(MIN_DUR) in export_service.generate_subtitle_text(did, "srt", "bilingual")

    def test_generate_subtitle_text_returns_the_same_text(self, isolated_db):
        did = _seed(isolated_db, ROWS)
        assert (export_service.generate_subtitle_text(did, "srt", "en")
                == export_service.generate_subtitle_export(did, "srt", "en")["text"])

    @pytest.mark.parametrize("fmt", ["vtt", "lrc"])
    def test_vtt_and_lrc_keep_todays_timing(self, isolated_db, fmt):
        did = _seed(isolated_db, ROWS)
        result = export_service.generate_subtitle_export(did, fmt, "en")
        clamped, _ = sf.clamp_overlaps(ROWS)
        expected = (sf.lines_to_vtt if fmt == "vtt" else sf.lines_to_lrc)(clamped, "en")
        assert result["text"] == expected and result["timing_adjusted_count"] == 0

    def test_ass_keeps_todays_timing(self, isolated_db):
        did = _seed(isolated_db, ROWS)
        text = export_service.generate_ass_text(did, per_speaker_colors=False)
        assert "0:00:00.00,0:00:00.30" in text
        assert "0:00:04.00,0:00:20.00" in text

    def test_route_reports_the_count_in_a_header(self, client, isolated_db):
        did = _seed(isolated_db, ROWS)
        resp = client.get(f"/api/export/dramas/{did}/subtitle?fmt=srt&field=en")
        assert resp.headers["X-Cues-Adjusted"] == "3"
        resp = client.get(f"/api/export/dramas/{did}/subtitle?fmt=vtt&field=en")
        assert resp.headers["X-Cues-Adjusted"] == "0"
