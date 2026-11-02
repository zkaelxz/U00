"""Talk-over lines (services/speaker_overlap_service.py): the turns are faked, no audio or model."""
import pytest

import diarize
from core import Line
from lib.errors import ConflictError, NotFoundError
from services import speaker_overlap_service as svc

# The shape of a real case: Uririn's turn runs 294.35-297.27, Anok's 296.02-296.93
# sits inside it, and no line carries Anok's voice there.
TURNS = [
    {"start": 288.965, "end": 292.728, "speaker": "S01"},
    {"start": 294.348, "end": 297.267, "speaker": "S00"},
    {"start": 296.018, "end": 296.930, "speaker": "S01"},
    {"start": 297.925, "end": 299.562, "speaker": "S00"},
    {"start": 299.967, "end": 304.625, "speaker": "S00"},
]


def _lines(*rows):
    return [Line(id=i + 1, idx=i, start=s, end=e, zh=f"行{i}", speaker=sp)
            for i, (s, e, sp) in enumerate(rows)]


BASE = (
    (289.0, 292.5, "Anok"),
    (294.09, 295.93, "Uririn"),
    (295.93, 296.61, "Uririn"),
    (297.65, 298.45, "Uririn"),
)


class TestFindTalkOver:
    def test_second_voice_under_a_line_gets_a_stretch_named_after_its_lines(self):
        assert svc.find_talk_over(TURNS, _lines(*BASE)) == [
            {"start": 296.018, "end": 296.93, "speaker": "Anok", "line_id": None}]

    def test_the_speakers_own_line_starting_just_after_is_named_as_the_late_one(self):
        lines = _lines(*BASE[:3], (297.65, 298.45, "Anok"))
        assert svc.find_talk_over(TURNS, lines) == [
            {"start": 296.018, "end": 296.93, "speaker": "Anok", "line_id": 4}]

    def test_a_line_that_starts_part_way_into_their_voice_is_late_too(self):
        lines = _lines(*BASE[:3], (296.45, 298.45, "Anok"))
        assert [x["line_id"] for x in svc.find_talk_over(TURNS, lines)] == [4]

    def test_a_line_that_starts_only_a_moment_late_is_left_alone(self):
        assert svc.find_talk_over(TURNS, _lines(*BASE[:3], (296.3, 298.45, "Anok"))) == []

    def test_a_line_far_after_is_not_the_late_one(self):
        lines = _lines(*BASE[:3], (299.0, 300.0, "Anok"))
        assert [x["line_id"] for x in svc.find_talk_over(TURNS, lines)] == [None]

    def test_a_speaker_who_already_has_a_line_there_is_left_alone(self):
        lines = _lines(*BASE, (296.0, 296.95, "Anok"))
        assert svc.find_talk_over(TURNS, lines) == []

    def test_a_blank_talk_over_line_added_before_counts_as_covering(self):
        lines = _lines(*BASE)
        lines.append(Line(id=9, idx=4, start=296.018, end=296.93, zh="", speaker="Anok"))
        assert svc.find_talk_over(TURNS, lines) == []

    def test_a_flicker_shorter_than_the_minimum_is_ignored(self):
        turns = [{"start": 0, "end": 5, "speaker": "S00"}, {"start": 2, "end": 2.3, "speaker": "S01"}]
        assert svc.find_talk_over(turns, _lines((0, 5, "Uririn"), (6, 7, "Anok"))) == []

    def test_an_overlap_nobody_has_a_line_over_is_a_gap_not_talk_over(self):
        assert svc.find_talk_over(TURNS, _lines((289.0, 292.5, "Anok"))) == []

    def test_a_label_with_no_lines_keeps_its_own_name(self):
        out = svc.find_talk_over(TURNS, _lines((294.09, 296.61, "Uririn")))
        assert out == [{"start": 296.018, "end": 296.93, "speaker": "S01", "line_id": None}]

    def test_touching_stretches_of_one_speaker_join(self):
        turns = [{"start": 0, "end": 10, "speaker": "S00"},
                 {"start": 2, "end": 4, "speaker": "S01"}, {"start": 4, "end": 6, "speaker": "S01"}]
        out = svc.find_talk_over(turns, _lines((0, 10, "Uririn")))
        assert [(x["start"], x["end"]) for x in out] == [(2.0, 6.0)]

    def test_bad_turns_are_skipped(self):
        assert svc.find_talk_over([{"start": "x", "end": 1, "speaker": "S0"}, {}], _lines(*BASE)) == []


def _drama(isolated_db, turns=TURNS, rows=BASE):
    sid = isolated_db.get_or_create_series("S")
    did = isolated_db.create_drama(title_en="D", series_id=sid, audio_filename="audio.wav",
                                   source_language="zh", status="aligned")
    isolated_db.save_lines(did, _lines(*rows))
    if turns is not None:
        diarize.save_turns(isolated_db.drama_dir(did), turns)
    return did


class TestAddTalkOverLines:
    def test_the_speakers_late_line_is_started_where_their_voice_begins(self, isolated_db):
        did = _drama(isolated_db, rows=(*BASE[:3], (297.65, 298.45, "Anok")))
        out = svc.add_talk_over_lines(did)
        assert (out["added_count"], out["moved_count"], out["new_line_ids"]) == (0, 1, [])
        lines = isolated_db.load_line_objects(did)
        assert len(lines) == 4
        assert (lines[3].start, lines[3].end, lines[3].zh) == (296.018, 298.45, "行3")
        assert (lines[2].start, lines[2].end) == (295.93, 296.61)
        assert svc.add_talk_over_lines(did)["moved_count"] == 0


    def test_adds_a_blank_flagged_line_for_the_second_voice_in_time_order(self, isolated_db):
        did = _drama(isolated_db)
        out = svc.add_talk_over_lines(did)
        assert out["added_count"] == 1 and out["history_id"]
        lines = isolated_db.load_line_objects(did)
        assert [ln.idx for ln in lines] == list(range(5))
        new = lines[3]
        assert new.id == out["new_line_ids"][0]
        assert (new.start, new.end, new.speaker, new.zh, new.en) == (296.018, 296.93, "Anok", "", "")
        assert new.flag == svc.TALK_OVER_FLAG and new.speaker_manual
        # The line it is spoken over keeps its time: the two overlap on purpose.
        assert (lines[2].start, lines[2].end) == (295.93, 296.61)
        assert [ln.zh for ln in lines if ln.zh] == ["行0", "行1", "行2", "行3"]

    def test_running_twice_adds_nothing_the_second_time(self, isolated_db):
        did = _drama(isolated_db)
        svc.add_talk_over_lines(did)
        again = svc.add_talk_over_lines(did)
        assert again["added_count"] == 0 and again["history_id"] is None
        assert len(isolated_db.load_line_objects(did)) == 5

    def test_nothing_to_add_writes_no_history(self, isolated_db):
        did = _drama(isolated_db, turns=[{"start": 0, "end": 3, "speaker": "S00"}])
        assert svc.add_talk_over_lines(did)["added_count"] == 0
        assert isolated_db.list_line_history(did) == []

    def test_no_saved_detection_is_a_conflict(self, isolated_db):
        with pytest.raises(ConflictError):
            svc.add_talk_over_lines(_drama(isolated_db, turns=None))

    def test_unknown_drama(self, isolated_db):
        with pytest.raises(NotFoundError):
            svc.add_talk_over_lines(999999)
