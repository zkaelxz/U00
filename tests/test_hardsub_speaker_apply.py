"""Putting a hard-sub reading's speaker groups on a title's lines (service and route)."""

import pytest

import db
from core import Line
from lib.errors import NotFoundError
from services import hardsub_speaker_apply_service as apply_svc
from services import hardsub_speaker_read_service as reading

GROUPS = [
    {"key": "white-left", "lines": [{"start": 0.0, "end": 3.0, "text": "你好吗"}]},
    {"key": "yellow-right", "lines": [{"start": 3.0, "end": 6.0, "text": "我很好"}]},
]


def _title(lines):
    did = db.create_drama(title_en="Four Voices", source_language="zh")
    db.save_lines(did, lines)
    return did


def _outcome(groups):
    return {"groups": groups, "windows_read": 2, "windows_total": 2, "unreadable": 0, "unclear_pictures": 0,
            "failed_reads": 0, "cost_usd": 0.0, "stopped": None, "note": None,
            "comparison": {"groups": [], "recall": None, "precision": None, "label_overlap": {},
                           "label_match": None}}


def _speakers(did):
    return [r["speaker"] for r in db.load_lines(did)]


def test_a_line_takes_the_group_that_covers_most_of_it(isolated_db):
    did = _title([Line(0, 0.2, 2.8, "你好吗", speaker="SPEAKER_00"), Line(1, 3.1, 5.9, "我很好", speaker="SPEAKER_00")])
    reading._save_result(did, _outcome(GROUPS))
    got = apply_svc.apply(None, did)
    assert _speakers(did) == ["white-left", "yellow-right"]
    assert got == {"labelled": 2, "kept_manual": 0, "unplaced": 0, "speakers": ["white-left", "yellow-right"]}


def test_a_line_no_group_covers_keeps_its_speaker(isolated_db):
    did = _title([Line(0, 7.0, 9.0, "再见", speaker="SPEAKER_01")])
    reading._save_result(did, _outcome(GROUPS))
    assert apply_svc.apply(None, did)["unplaced"] == 1
    assert _speakers(did) == ["SPEAKER_01"]


def test_a_speaker_set_by_hand_is_kept(isolated_db):
    did = _title([Line(0, 0.0, 3.0, "你好吗", speaker="Mei", speaker_manual=True)])
    reading._save_result(did, _outcome(GROUPS))
    assert apply_svc.apply(None, did)["kept_manual"] == 1
    assert _speakers(did) == ["Mei"]


def test_two_groups_covering_a_line_equally_leave_it_alone():
    both = [{"key": "a", "lines": [{"start": 0.0, "end": 2.0, "text": "x"}]},
            {"key": "b", "lines": [{"start": 0.0, "end": 2.0, "text": "y"}]}]
    assert apply_svc.label_for(Line(0, 0.0, 2.0, "x"), both) is None


def test_a_brushing_caption_does_not_label_a_line():
    assert apply_svc.label_for(Line(0, 2.5, 6.0, "x"), GROUPS[:1]) is None


def test_the_labels_become_characters(isolated_db):
    did = _title([Line(0, 0.0, 3.0, "你好吗")])
    reading._save_result(did, _outcome(GROUPS))
    apply_svc.apply(None, did)
    assert "white-left" in {c["speaker_label"] for c in db.list_characters(did)}


def test_a_title_without_a_finished_reading_is_not_found(isolated_db):
    did = _title([Line(0, 0.0, 3.0, "你好吗")])
    with pytest.raises(NotFoundError):
        apply_svc.apply(None, did)


def test_a_reading_that_found_no_groups_changes_nothing(isolated_db):
    did = _title([Line(0, 0.0, 3.0, "你好吗", speaker="S1")])
    reading._save_result(did, _outcome([]))
    with pytest.raises(NotFoundError):
        apply_svc.apply(None, did)
    assert _speakers(did) == ["S1"]


def test_the_route_is_pc_only_and_returns_the_counts(isolated_db):
    from tests.test_api_benchmark import BASE, LOCAL_HDR, _admin, _local, _remote
    did = _title([Line(0, 0.0, 3.0, "你好吗")])
    reading._save_result(did, _outcome(GROUPS))
    url = f"{BASE}/dramas/{did}/hard-sub-reading/apply"
    assert _remote().post(url, headers={**_admin(), **LOCAL_HDR}).status_code == 403
    r = _local().post(url, headers=LOCAL_HDR)
    assert r.status_code == 200, r.text
    assert r.json()["labelled"] == 1
    assert db.drama_dir(did) not in r.text
