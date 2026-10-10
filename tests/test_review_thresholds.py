"""Review-check thresholds by content profile (review_thresholds)."""
import core
import review_thresholds as rt
import subtitle_formats
import translate_engines
from core import Line
from services import export_service, line_tools_service
from services import review_lines_service as svc
from tests.streamer_transcript import streamer_lines

STREAMER = {"content_mode": "streamer_vod"}


def _seed(db, lines, **fields):
    did = db.create_drama(title_en="D", **fields)
    db.save_lines(did, lines)
    return did


def _flagged_share(db, did) -> float:
    lines = core.lines_from_rows(db.load_lines(did))
    cov = svc.get_coverage_report(did)
    idx = {e["idx"] for e in cov["long_lines"]} | {e["before_idx"] for e in cov["large_gaps"]}
    idx |= {f["idx"] for f in svc.get_pacing_flags(did)["flags"]}
    idx |= {ln.idx for ln, _, _ in subtitle_formats.dense_lines(
        lines, mode=subtitle_formats.flagging_mode_of(db.get_drama(did)))}
    return len(idx) / len(lines)


class TestProfileSelection:
    def test_streamer_by_content_mode_or_media_type(self):
        assert rt.profile_of({"content_mode": "streamer_vod"}) == rt.PROFILE_STREAMER
        assert rt.profile_of({"media_type": "streamer_vod"}) == rt.PROFILE_STREAMER

    def test_everything_else_is_default(self):
        for d in (None, {}, {"content_mode": "audio_drama", "media_type": "anime"},
                  {"content_mode": "novel_narration"}):
            assert rt.thresholds_for(d) is rt.DEFAULT_THRESHOLDS

    def test_default_profile_matches_the_function_defaults(self):
        t = rt.DEFAULT_THRESHOLDS
        assert (t.gap_seconds, t.long_duration_seconds) == (3.0, 12.0)
        assert (t.pacing_wpm, t.too_long_margin, t.short_min_seconds, t.short_ratio) == (160, 1.15, 1.2, 0.4)


class TestPacingThresholds:
    def _line(self, words, dur):
        return Line(idx=0, start=0, end=dur, zh="字", en=" ".join(["w"] * words))

    def test_margin_decides_too_long(self):
        ln = self._line(8, 2.5)  # 3 s needed at 160 wpm: 1.2x the slot
        assert translate_engines.smart_segment_lines([ln])[0]["issue"] == "too_long_for_slot"
        assert translate_engines.smart_segment_lines([ln], too_long_margin=1.5) == []

    def test_short_ratio_and_min_seconds_decide_very_short(self):
        ln = self._line(2, 5.0)  # 0.75 s needed of 5 s
        assert translate_engines.smart_segment_lines([ln])[0]["issue"] == "very_short_relative_to_slot"
        assert translate_engines.smart_segment_lines([ln], short_ratio=0.1) == []
        assert translate_engines.smart_segment_lines([ln], min_seconds=6.0) == []

    def test_severity_orders_worst_first(self):
        flags = translate_engines.smart_segment_lines([self._line(8, 2.5), self._line(30, 2.5)])
        assert flags[1]["severity"] > flags[0]["severity"]


class TestCoverageThresholds:
    def test_gap_threshold(self):
        lines = [Line(idx=0, start=0, end=2, zh="你好", en="a"), Line(idx=1, start=10, end=12, zh="你好", en="b")]
        assert len(core.diagnose_line_coverage(lines)["large_gaps"]) == 1
        assert core.diagnose_line_coverage(lines, **rt.STREAMER_THRESHOLDS.coverage_kwargs())["large_gaps"] == []

    def test_long_line_threshold(self):
        lines = [Line(idx=0, start=0, end=15, zh="你好吗你好吗", en="a")]
        assert len(core.diagnose_line_coverage(lines)["long_lines"]) == 1
        assert core.diagnose_line_coverage(lines, **rt.STREAMER_THRESHOLDS.coverage_kwargs())["long_lines"] == []


class TestReadingSpeedMode:
    LINE = Line(idx=0, start=0, end=3, zh="x", en="a" * 54)  # 18 cps

    def test_streamer_normal_runs_at_the_streamer_limits(self):
        assert subtitle_formats.flagging_mode_of(STREAMER) == subtitle_formats.STREAMER_MODE
        assert len(subtitle_formats.dense_lines([self.LINE], mode="relaxed")) == 1
        assert subtitle_formats.dense_lines([self.LINE], mode=subtitle_formats.STREAMER_MODE) == []
        assert len(subtitle_formats.dense_lines([Line(idx=0, start=0, end=3, zh="x", en="a" * 60)],
                                                mode=subtitle_formats.STREAMER_MODE)) == 1

    def test_explicit_choices_and_other_titles_unchanged(self):
        assert subtitle_formats.flagging_mode_of({}) == "normal"
        assert subtitle_formats.flagging_mode_of({"reading_speed_mode": "normal", "content_mode": "audio_drama"}) == "normal"
        assert subtitle_formats.flagging_mode_of({**STREAMER, "reading_speed_mode": "relaxed"}) == "relaxed"
        assert subtitle_formats.flagging_mode_of({**STREAMER, "reading_speed_mode": "off"}) == "off"

    def test_stored_mode_api_value_is_never_the_internal_mode(self, isolated_db):
        did = _seed(isolated_db, [], content_mode="streamer_vod")
        assert export_service.get_reading_speed_mode(did) == {"mode": "normal"}
        assert subtitle_formats.STREAMER_MODE not in subtitle_formats.READING_SPEED_MODES


class TestSyntheticStreamerTranscript:
    def test_streamer_profile_flags_a_sane_share_default_does_not(self, isolated_db):
        lines = streamer_lines()
        drama_default = _seed(isolated_db, lines)
        drama_streamer = _seed(isolated_db, streamer_lines(), content_mode="streamer_vod")
        assert _flagged_share(isolated_db, drama_default) > 0.85
        assert _flagged_share(isolated_db, drama_streamer) < 0.25

    def test_real_outliers_still_flagged_in_both_profiles(self, isolated_db):
        def build():
            ls = streamer_lines(200)
            last = ls[-1]
            # 30 s for 5 characters, then a 70 s silence after it.
            ls.append(Line(idx=200, start=last.end + 1, end=last.end + 31, zh="五个字符呢", en="Hi"))
            ls.append(Line(idx=201, start=last.end + 101, end=last.end + 103, zh="你好", en="Hello"))
            return ls
        for fields in ({}, {"content_mode": "streamer_vod"}):
            did = _seed(isolated_db, build(), **fields)
            cov = svc.get_coverage_report(did)
            assert [e["idx"] for e in cov["long_lines"]] == [200]
            assert any(g["after_idx"] == 200 for g in cov["large_gaps"])

    def test_gap_size_decides_by_profile(self, isolated_db):
        def pair(gap):
            return [Line(idx=0, start=0, end=3, zh="你好", en="Hi"),
                    Line(idx=1, start=3 + gap, end=6 + gap, zh="你好", en="Hi")]
        flagged = lambda did: len(svc.get_coverage_report(did)["large_gaps"])
        streamer = {"content_mode": "streamer_vod"}
        assert flagged(_seed(isolated_db, pair(20))) == 1
        assert flagged(_seed(isolated_db, pair(20), **streamer)) == 1
        assert flagged(_seed(isolated_db, pair(10))) == 1
        assert flagged(_seed(isolated_db, pair(10), **streamer)) == 0

    def test_pacing_flags_are_worst_first(self, isolated_db):
        did = _seed(isolated_db, streamer_lines(300), content_mode="streamer_vod")
        sev = [f["severity"] for f in svc.get_pacing_flags(did)["flags"]]
        assert sev == sorted(sev, reverse=True)

    def test_shorten_uses_the_same_thresholds_as_the_pacing_list(self, isolated_db):
        ls = [Line(idx=0, start=0, end=2.5, zh="字", en=" ".join(["w"] * 8))]  # 1.2x the slot
        d, s = _seed(isolated_db, ls), _seed(isolated_db, [Line(**ls[0].__dict__)], content_mode="streamer_vod")
        assert len(line_tools_service._too_long_lines(ls, isolated_db.get_drama(d))) == 1
        assert line_tools_service._too_long_lines(ls, isolated_db.get_drama(s)) == []

    def test_saved_flags_survive_a_threshold_change(self, isolated_db):
        ls = streamer_lines(20)
        ls[3].flag, ls[3].flag_note = subtitle_formats.READING_SPEED_FLAG, "old"
        did = _seed(isolated_db, ls, content_mode="streamer_vod")
        export_service.flag_dense_lines(did)
        assert [r["flag"] for r in isolated_db.load_lines(did)][3] == subtitle_formats.READING_SPEED_FLAG
