"""glossary_name_report_service: one name-check report across every title the caller may see.
Fabricated names only; Korean is the source language because it needs no reading library."""
import json
import threading

import pytest

import background_jobs
import db
from core import Line
from services import glossary_name_report_service as report
from services.service_errors import ConflictError, InvalidInputError

ADA = {"user_id": 11, "is_admin": False, "is_local_owner": False, "permissions": ["lines.read"]}
BEN = {"user_id": 12, "is_admin": False, "is_local_owner": False, "permissions": ["lines.read"]}
ADMIN = {"user_id": 1, "is_admin": True, "is_local_owner": False, "permissions": ["lines.read"]}
LOCAL = {"user_id": None, "is_admin": False, "is_local_owner": True, "permissions": ["lines.read"]}


def _title(name, texts, language="ko", owner=None, private=0, terms=()):
    sid = db.get_or_create_series(f"Series of {name}")
    did = db.create_drama(title_en=name, series_id=sid, source_language=language)
    if owner is not None:
        # Series carry the privacy flag of a title that has one, so write both.
        with db.get_conn() as conn:
            conn.execute("UPDATE dramas SET owner_user_id = ?, is_private = ?, series_id = NULL WHERE id = ?",
                         (owner, private, did))
    db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=t, en="") for i, t in enumerate(texts)])
    if owner is None:
        for original, english, aliases in terms:
            db.upsert_glossary_term(sid, original, english, aliases="|".join(aliases))
    return did


def _three_titles():
    a = _title("Alpha Stream", ["Yalo said hi", "then Yalo left", "later Yalo ran"])
    b = _title("Beta Stream", ["Yaloch said hi", "then Yaloch left", "nothing here"])
    c = _title("Gamma Stream", ["Yalou said hi", "then Yalou left", "and Yalo ran"])
    return a, b, c


def _scan(visible_to=None, **kw):
    return report.scan_titles(visible_to, **kw)


def _group_with(result, text):
    return next(g for g in result["groups"] if text in {v["text"] for v in g["variants"]})


def test_one_name_heard_differently_in_three_titles_is_one_group(isolated_db):
    a, b, c = _three_titles()
    result = _scan()
    assert result["titles_scanned"] == 3 and result["truncated"] is False
    group = _group_with(result, "Yaloch")
    assert {v["text"]: v["count"] for v in group["variants"]} == {"Yalo": 4, "Yaloch": 2, "Yalou": 2}
    assert group["total"] == 8 and group["titles_total"] == 3
    per_title = {t["drama_id"]: t for t in group["titles"]}
    assert set(per_title) == {a, b, c}
    assert per_title[a]["title"] == "Alpha Stream" and per_title[a]["count"] == 3
    assert per_title[c]["count"] == 3
    assert group["canonical"] == "Yalo"
    assert 1 <= len(group["examples"]) <= report.MAX_GROUP_EXAMPLES
    assert {e["drama_id"] for e in group["examples"]} <= {a, b, c}
    assert "mark" not in group and len(result["groups"]) == 1   # marks are applied on read, not by the scan


def test_examples_are_short_snippets_not_transcripts(isolated_db):
    long_line = "x" * 300 + " Yalo " + "y" * 300
    _title("Long", [long_line, long_line + " Yalo", "Yalo again"])
    _title("Other", ["Yaloch here", "Yaloch there"])
    group = _group_with(_scan(), "Yalo")
    assert group["examples"]
    assert all(len(e["snippet"]) <= report.MAX_SNIPPET_CHARS for e in group["examples"])
    assert len(json.dumps(group)) < 3000


def test_a_name_in_one_title_with_one_spelling_is_left_to_the_per_title_tool(isolated_db):
    _title("Solo", ["Mirel said hi", "then Mirel left"])
    result = _scan()
    assert result["groups"] == [] and result["singletons_omitted"] == 1


def test_a_member_never_sees_another_members_title_or_lines(isolated_db):
    _title("Shared Stream", ["Yalo said hi", "then Yalo left"])
    _title("Ada Private", ["Yaloch said hi", "then Yaloch left"], owner=11, private=1)
    _title("Ben Private", ["Yalou said hi", "then Yalou left", "Secretname Secretname"], owner=12, private=1)
    from services import ownership_service
    ada = _scan(ownership_service.visible_to_filter(ADA))
    text = json.dumps(ada)
    assert "Ben Private" not in text and "Yalou" not in text and "Secretname" not in text
    assert {t["title"] for g in ada["groups"] for t in g["titles"]} == {"Shared Stream", "Ada Private"}
    assert ada["titles_total"] == 2
    for principal in (None, LOCAL, ADMIN):
        everything = _scan(ownership_service.visible_to_filter(principal))
        assert everything["titles_total"] == 3
        assert "Ben Private" in json.dumps(everything)


def test_caps_are_respected(isolated_db, monkeypatch):
    for i in range(5):
        _title(f"Stream {i}", [f"Yalo{'ch' if i % 2 else ''} said hi", f"then Yalo{'ch' if i % 2 else ''} left"])
    monkeypatch.setattr(report, "MAX_TITLES_SCANNED", 3)
    result = _scan()
    assert result["titles_total"] == 5 and result["titles_scanned"] == 3 and result["truncated"] is True
    monkeypatch.setattr(report, "MAX_TITLES_SCANNED", 60)
    monkeypatch.setattr(report, "MAX_TOTAL_EXAMPLES", 1)
    assert sum(len(g["examples"]) for g in _scan()["groups"]) == 1
    monkeypatch.setattr(report, "MAX_TOTAL_EXAMPLES", 300)
    monkeypatch.setattr(report, "MAX_GROUP_TITLES", 2)
    group = _scan()["groups"][0]
    assert len(group["titles"]) == 2 and group["titles_total"] == 5
    monkeypatch.setattr(report, "MAX_GROUP_TITLES", 20)
    monkeypatch.setattr(report, "MAX_RESPONSE_BYTES", 400)
    small = _scan()
    assert small["truncated"] is True and small["groups_omitted"] >= 1
    assert len(json.dumps(small)) <= 400 + 1500   # the envelope around the groups is small and fixed


def test_a_time_budget_stops_the_scan_and_says_so(isolated_db, monkeypatch):
    _three_titles()
    monkeypatch.setattr(report, "MAX_SCAN_SECONDS", -1)
    result = _scan()
    assert result["truncated"] is True and result["titles_scanned"] == 0


def _snapshot():
    with db.get_conn() as conn:
        return {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall()]
                for t in ("dramas", "lines", "glossary_terms", "app_settings", "series")}


def test_the_scan_writes_nothing(isolated_db):
    _three_titles()
    before = _snapshot()
    _scan()
    assert _snapshot() == before


def test_a_partly_covered_cluster_reports_the_english(isolated_db):
    a = _title("Alpha Stream", ["Yalo said hi", "then Yalo left", "Yaloch ran", "Yaloch sat"],
               terms=[("Yalo", "Yarrow", [])])
    b = _title("Beta Stream", ["Yalou said hi", "then Yalou left"])
    group = _group_with(_scan(), "Yaloch")
    entry = next(t for t in group["titles"] if t["drama_id"] == a)
    other = next(t for t in group["titles"] if t["drama_id"] == b)
    assert other["in_glossary"] is False and other["glossary_english"] is None
    assert entry["in_glossary"] is True and entry["glossary_term"] == "Yalo"
    assert entry["glossary_english"] == "Yarrow"
    assert group["canonical"] == "Yalo"   # a name a glossary already holds is offered first


def test_an_unavailable_language_is_counted_not_fatal(isolated_db, monkeypatch):
    _three_titles()
    _title("Needs Library", ["ヤロ", "ヤロ"], language="ja")
    monkeypatch.setattr(report.ph, "availability",
                        lambda lang: {"available": lang != "ja", "package": "pykakasi",
                                      "hint": "Japanese needs pykakasi."})
    result = _scan()
    assert result["titles_unavailable"] == 1 and result["reasons"] == ["Japanese needs pykakasi."]
    assert result["groups"]


def test_a_title_that_fails_is_skipped_without_its_error(isolated_db, monkeypatch):
    a, b, c = _three_titles()
    real = report.gv.discover_names

    def flaky(drama_id):
        if drama_id == b:
            raise RuntimeError("C:\\secret\\path\\library.db is locked")
        return real(drama_id)

    monkeypatch.setattr(report.gv, "discover_names", flaky)
    result = _scan()
    assert result["titles_failed"] == 1 and result["titles_scanned"] == 3
    assert "secret" not in json.dumps(result)


# --- marks -----------------------------------------------------------------------------

def test_marks_persist_and_hide_the_group_from_the_unresolved_list(isolated_db):
    _three_titles()
    key = _group_with(_scan(), "Yalo")["key"]
    assert report.set_mark(ADA, key, "wrong", _scan()) == {"key": key, "mark": "wrong"}
    again = report.apply_marks(ADA, _scan())
    group = _group_with(again, "Yaloch")
    assert group["mark"] == "wrong"
    assert again["counts"] == {"unresolved": 0, "right": 0, "wrong": 1, "skip": 0}
    # A different person's marks are their own.
    assert report.apply_marks(BEN, _scan())["counts"]["unresolved"] == 1
    report.set_mark(ADA, key, "clear", _scan())
    assert report.apply_marks(ADA, _scan())["counts"]["unresolved"] == 1


def test_right_skip_and_a_bad_mark(isolated_db):
    _three_titles()
    result = _scan()
    key = result["groups"][0]["key"]
    for mark in ("right", "skip"):
        report.set_mark(LOCAL, key, mark, result)
        assert report.apply_marks(LOCAL, result)["groups"][0]["mark"] == mark
    with pytest.raises(InvalidInputError):
        report.set_mark(LOCAL, key, "maybe", result)
    with pytest.raises(InvalidInputError):
        report.set_mark(LOCAL, "not a held group", "wrong", result)


def test_the_number_of_marks_is_bounded(isolated_db, monkeypatch):
    _three_titles()
    result = _scan()
    monkeypatch.setattr(report, "MAX_MARKS", 1)
    report.set_mark(LOCAL, result["groups"][0]["key"], "skip", result)
    extra = dict(result["groups"][0], key="other name", variants=[{"text": "Other name", "count": 2}])
    with pytest.raises(ConflictError):
        report.set_mark(LOCAL, "other name", "skip", {**result, "groups": [extra]})
    # Changing an existing mark is never refused by the cap.
    report.set_mark(LOCAL, result["groups"][0]["key"], "right", result)


# --- export -----------------------------------------------------------------------------

def test_export_lists_only_unresolved_groups_with_names_counts_and_examples(isolated_db):
    _three_titles()
    _title("Delta", ["Mirel one", "Mirel two", "Mirelle three", "Mirelle four"])
    result = _scan()
    resolved = _group_with(result, "Yaloch")
    report.set_mark(LOCAL, resolved["key"], "wrong", result)
    text = report.export_markdown(report.apply_marks(LOCAL, result))
    assert text.startswith("# Name check across titles")
    assert "Mirel" in text and "Mirelle" in text and "Yaloch" not in text
    assert "Alpha Stream" not in text and "Delta" not in text    # counts only; no title names
    assert "Mirel one" in text                                    # an example line
    assert text.count("\n- ") >= 2


def test_export_with_nothing_to_report(isolated_db):
    assert "No unresolved names" in report.export_markdown(report.apply_marks(LOCAL, _scan()))


# --- the background job -------------------------------------------------------------------

def _wait(job_id):
    assert background_jobs.wait_for_job_threads(10, [job_id])


def test_a_started_scan_is_read_back_by_the_person_who_started_it_only(isolated_db):
    _three_titles()
    report.start_scan(ADA)
    _wait(report.job_id_for(ADA))
    status = report.get_report(ADA)
    assert status["status"] == "done" and status["result"]["groups"]
    assert status["result"]["counts"]["unresolved"] == 1
    assert report.get_report(BEN)["status"] == "idle" and report.get_report(BEN)["result"] is None


def test_only_one_scan_runs_at_a_time(isolated_db, monkeypatch):
    _three_titles()
    gate = threading.Event()
    real = report.scan_titles

    def slow(*args, **kwargs):
        gate.wait(5)
        return real(*args, **kwargs)

    monkeypatch.setattr(report, "scan_titles", slow)
    try:
        report.start_scan(ADA)
        with pytest.raises(ConflictError):
            report.start_scan(ADA)
        with pytest.raises(ConflictError):
            report.start_scan(BEN)
    finally:
        gate.set()
        _wait(report.job_id_for(ADA))


def test_a_running_scan_can_be_cancelled(isolated_db, monkeypatch):
    _three_titles()
    seen = threading.Event()
    gate = threading.Event()
    real = report.scan_titles

    def waits_for_cancel(visible_to, check, progress):
        seen.set()
        gate.wait(5)
        check()
        return real(visible_to, check, progress)

    monkeypatch.setattr(report, "scan_titles", waits_for_cancel)
    report.start_scan(ADA)
    assert seen.wait(5)
    assert report.cancel_scan(ADA)["cancel_requested"] is True
    gate.set()
    _wait(report.job_id_for(ADA))
    assert report.get_report(ADA)["status"] == "cancelled"
    with pytest.raises(ConflictError):
        report.cancel_scan(ADA)


def test_a_failed_scan_shows_a_fixed_message_not_the_error(isolated_db, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("cannot open C:\\Users\\someone\\library.db")

    monkeypatch.setattr(report, "scan_titles", boom)
    report.start_scan(ADA)
    _wait(report.job_id_for(ADA))
    status = report.get_report(ADA)
    assert status["status"] == "error" and "someone" not in json.dumps(status)
    assert status["message"]


def test_a_scan_started_for_a_member_scans_only_what_they_could_see(isolated_db):
    _title("Shared", ["Yalo said hi", "then Yalo left"])
    _title("Ben Private", ["Yaloch said hi", "then Yaloch left"], owner=12, private=1)
    report.start_scan(ADA)
    _wait(report.job_id_for(ADA))
    result = report.get_report(ADA)["result"]
    assert result["titles_total"] == 1 and "Ben Private" not in json.dumps(result)
