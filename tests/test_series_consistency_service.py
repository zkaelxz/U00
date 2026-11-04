"""services/series_consistency_service.py: the cross-episode rendering report."""

import pytest

import db
from core import Line
from engine_backends.glossary_check import GLOSSARY_FLAG
from services import series_consistency_service as svc
from lib.errors import NotFoundError

LOCAL = {"user_id": None, "is_admin": True, "is_local_owner": True}


def _episode(series_id, n, pairs, **extra):
    did = db.create_drama(title_en=f"Ep {n}", source_language="zh", series_id=series_id,
                          episode_number=n, **extra)
    db.save_lines(did, [Line(idx=i, start=i * 5, end=i * 5 + 4, zh=zh, en=en)
                        for i, (zh, en) in enumerate(pairs)])
    return did


@pytest.fixture
def saga(isolated_db):
    sid = db.create_series("Saga")
    db.upsert_glossary_term(sid, "雅罗", "Yalo", aliases="小雅", banned_translations=None)
    return sid


def _term(report, original="雅罗"):
    return next(t for t in report["terms"] if t["term_original"] == original)


def test_groups_renderings_across_episodes_and_flags_the_odd_one(saga):
    _episode(saga, 1, [("雅罗来了", "Yalo is here."), ("雅罗走了", "Yalo left."),
                       ("小雅笑了", "Yalo laughed.")])
    e2 = _episode(saga, 2, [("雅罗说话", "Yaloch speaks."), ("雅罗又说", "Yaloch speaks again."),
                            ("雅罗好", "Yalo is fine.")])
    report = svc.build_report(LOCAL, saga)
    term = _term(report)
    by_text = {r["text"]: r for r in term["renderings"]}
    assert by_text["Yalo"]["kind"] == "glossary" and by_text["Yalo"]["count"] == 4
    assert not by_text["Yalo"]["flagged"]
    assert by_text["Yaloch"]["flagged"] and by_text["Yaloch"]["count"] == 2
    ids = [r["id"] for r in db.load_lines(e2)]
    assert by_text["Yaloch"]["titles"] == [{"drama_id": e2, "line_count": 2, "line_ids": ids[:2]}]
    assert {x["drama_id"] for x in by_text["Yaloch"]["examples"]} == {e2}
    assert term["flagged_lines"] == 2
    assert report["summary"]["names_with_several_renderings"] == 1
    assert report["summary"]["flagged_lines"] == 2
    assert report["summary"]["titles_scanned"] == 2 and report["summary"]["lines_scanned"] == 6


def test_consistent_series_has_nothing_flagged(saga):
    _episode(saga, 1, [("雅罗来了", "Yalo is here.")])
    _episode(saga, 2, [("雅罗走了", "Yalo left.")])
    report = svc.build_report(LOCAL, saga)
    assert report["summary"]["names_with_several_renderings"] == 0
    assert report["summary"]["flagged_lines"] == 0
    assert [r["kind"] for r in _term(report)["renderings"]] == ["glossary"]


def test_a_line_with_no_name_is_listed_but_never_flagged(saga):
    _episode(saga, 1, [("雅罗来了", "Yalo is here."), ("雅罗走了", "she left.")])
    term = _term(svc.build_report(LOCAL, saga))
    unnamed = next(r for r in term["renderings"] if r["kind"] == "unnamed")
    assert unnamed["count"] == 1 and not unnamed["flagged"] and unnamed["titles"][0]["line_ids"] == []
    assert term["flagged_lines"] == 0


def test_counts_glossary_missing_flags_that_still_miss(saga):
    did = _episode(saga, 1, [("雅罗来了", "Yaloch is here."), ("雅罗走了", "Yalo left.")])
    lines = db.load_line_objects(did)
    for ln in lines:
        ln.flag = GLOSSARY_FLAG
    db.save_lines(did, lines, fields=("flag",))
    # The second line now has the name, so its leftover flag is no longer a miss.
    assert svc.build_report(LOCAL, saga)["summary"]["glossary_missing_lines"] == 1


def test_report_changes_nothing(saga):
    did = _episode(saga, 1, [("雅罗来了", "Yaloch is here.")])
    before = db.load_lines(did)
    svc.build_report(LOCAL, saga)
    assert db.load_lines(did) == before


def test_other_terms_names_are_not_taken_for_a_rendering(saga):
    db.upsert_glossary_term(saga, "苏", "Su", aliases=None, banned_translations=None)
    _episode(saga, 1, [("雅罗和苏来了", "Yalo and Su came."), ("雅罗和苏走了", "Su and Yalo left.")])
    assert _term(svc.build_report(LOCAL, saga))["flagged_lines"] == 0


def test_someone_elses_private_series_is_not_found(isolated_db):
    a = db.auth_create_user("a@example.com")
    b = db.auth_create_user("b@example.com")
    sid = db.create_series("Mine", owner_user_id=a, is_private=True)
    other = {"user_id": b, "is_admin": False, "is_local_owner": False, "admin_override": False}
    with pytest.raises(NotFoundError):
        svc.build_report(other, sid)
