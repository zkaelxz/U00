"""glossary_name_report_service: one name-check report across every title the caller may see.
Fabricated names only; Korean is the source language because it needs no reading library."""
import json
import threading
import time

import pytest

import background_jobs
from jobs import job_runner
import db
from core import Line
from services import glossary_name_report_service as report
from services import ownership_service
from services.service_errors import ConflictError, ForbiddenError, InvalidInputError, RateLimitedError

ADA = {"user_id": 11, "is_admin": False, "is_local_owner": False, "permissions": ["lines.read"]}
BEN = {"user_id": 12, "is_admin": False, "is_local_owner": False, "permissions": ["lines.read"]}
ADMIN = {"user_id": 1, "is_admin": True, "is_local_owner": False, "permissions": ["lines.read"]}
ADMIN_AT_PC = {**ADMIN, "admin_override": True}
LOCAL = {"user_id": None, "is_admin": False, "is_local_owner": True, "permissions": ["lines.read"]}


@pytest.fixture(autouse=True)
def _fresh_start_times(monkeypatch):
    # The start limit is per person and module-wide; every test starts from a clean slate.
    monkeypatch.setattr(report, "_last_start", {}, raising=False)


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
    assert job_runner.wait_for_job_threads(10, [job_id])


def test_a_started_scan_is_read_back_by_the_person_who_started_it_only(isolated_db):
    _three_titles()
    report.start_scan(ADA)
    _wait(report.job_id_for(ADA))
    status = report.get_report(ADA)
    assert status["status"] == "done" and status["result"]["groups"]
    assert status["result"]["counts"]["unresolved"] == 1
    assert report.get_report(BEN)["status"] == "idle" and report.get_report(BEN)["result"] is None


def _gated_scan(monkeypatch):
    gate = threading.Event()
    real = report.scan_titles

    def slow(*args, **kwargs):
        gate.wait(5)
        return real(*args, **kwargs)

    monkeypatch.setattr(report, "scan_titles", slow)
    return gate


def test_one_scan_per_person_and_at_most_two_at_once(isolated_db, monkeypatch):
    _three_titles()
    gate = _gated_scan(monkeypatch)
    try:
        report.start_scan(ADA)
        with pytest.raises(ConflictError):
            report.start_scan(ADA)                     # her own is still running
        report.start_scan(LOCAL)                       # the second slot: the PC owner is not locked out
        with pytest.raises(ConflictError) as refused:
            report.start_scan(BEN)                     # a third
        assert "two" in str(refused.value).lower()
    finally:
        gate.set()
        _wait(report.job_id_for(ADA))
        _wait(report.job_id_for(LOCAL))


def test_a_person_cannot_restart_scans_back_to_back(isolated_db):
    _three_titles()
    report.start_scan(ADA)
    _wait(report.job_id_for(ADA))
    with pytest.raises(RateLimitedError):
        report.start_scan(ADA)
    report.start_scan(BEN)                             # someone else is not limited by her start
    _wait(report.job_id_for(BEN))


def test_the_start_limit_lapses(isolated_db, monkeypatch):
    _three_titles()
    report.start_scan(ADA)
    _wait(report.job_id_for(ADA))
    monkeypatch.setattr(report, "MIN_START_INTERVAL_SECONDS", 0)
    report.start_scan(ADA)
    _wait(report.job_id_for(ADA))


def test_the_pc_owner_and_admins_can_cancel_anyones_scan_a_member_cannot(isolated_db, monkeypatch):
    _three_titles()
    gate = _gated_scan(monkeypatch)
    try:
        report.start_scan(ADA)
        ada_job = report.job_id_for(ADA)
        with pytest.raises(ForbiddenError):
            report.cancel_scan(BEN, job_id=ada_job)
        # An admin may stop anyone's job from away too (owner decision 2026-10-12).
        assert report.cancel_scan(ADMIN, job_id=ada_job)["cancel_requested"] is True
        with pytest.raises(InvalidInputError):
            report.cancel_scan(LOCAL, job_id="translate_1")
        assert report.cancel_scan(ADMIN_AT_PC, job_id=ada_job)["cancel_requested"] is True
        assert background_jobs.is_cancel_requested(ada_job)
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


# --- a title that goes private after the scan ----------------------------------------------

def _make_private(drama_id, owner):
    with db.get_conn() as conn:
        conn.execute("UPDATE dramas SET owner_user_id = ?, is_private = 1 WHERE id = ?", (owner, drama_id))


def test_a_title_made_private_after_the_scan_vanishes_from_the_members_report_and_export(isolated_db):
    _title("Alpha Stream", ["Yalo said hi", "then Yalo left"], owner=11, private=0)
    beta = _title("Beta Stream", ["Yaloch said hi", "then Yaloch left", "Mirel one", "Mirelle two"], owner=11, private=0)
    _title("Gamma Stream", ["Yalou said hi", "then Yalou left"], owner=11, private=0)
    _title("Delta Stream", ["Mirel here", "Mirelle there"], owner=11, private=0)
    report.start_scan(BEN)
    _wait(report.job_id_for(BEN))
    before = json.dumps(report.get_report(BEN)["result"])
    assert "Beta Stream" in before and "Yaloch" in before
    _make_private(beta, owner=11)
    after = report.get_report(BEN)["result"]
    text = json.dumps(after) + report.export_report(BEN)
    assert "Beta Stream" not in text and "Yaloch" not in text and "then Yaloch left" not in text
    yalo = _group_with(after, "Yalo")
    assert {v["text"] for v in yalo["variants"]} == {"Yalo", "Yalou"}
    assert yalo["titles_total"] == 2 and yalo["total"] == 4
    assert after["counts"]["unresolved"] == len(after["groups"])
    # The owner of the title still sees it.
    visible = report.scan_titles(ownership_service.visible_to_filter(ADA))
    assert "Beta Stream" in json.dumps(visible)


def test_a_group_left_with_no_visible_title_is_dropped_and_the_counts_follow(isolated_db):
    _title("Alpha Stream", ["Yalo said hi", "then Yalo left"], owner=11, private=0)
    _title("Gamma Stream", ["Yalou said hi", "then Yalou left"], owner=11, private=0)
    solo1 = _title("Solo One", ["Mirel one", "Mirelle two"], owner=11, private=0)
    report.start_scan(BEN)
    _wait(report.job_id_for(BEN))
    assert report.get_report(BEN)["result"]["counts"]["unresolved"] == 2
    _make_private(solo1, owner=11)
    after = report.get_report(BEN)["result"]
    assert [g["canonical"] for g in after["groups"]] == ["Yalo"] and "Mirel" not in json.dumps(after)
    assert after["counts"]["unresolved"] == 1


def test_marking_works_on_the_view_the_member_can_still_see(isolated_db):
    _title("Alpha Stream", ["Yalo said hi", "then Yalo left"], owner=11, private=0)
    beta = _title("Beta Stream", ["Yaloch said hi", "then Yaloch left"], owner=11, private=0)
    report.start_scan(BEN)
    _wait(report.job_id_for(BEN))
    _make_private(beta, owner=11)
    group = report.get_report(BEN)["result"]["groups"][0]
    assert group["titles_total"] == 1 and [v["text"] for v in group["variants"]] == ["Yalo"]
    assert report.set_mark(BEN, group["key"], "skip")["mark"] == "skip"


# --- the merge step is bounded and stoppable -------------------------------------------------

def _synthetic_records(n, spellings_per=3):
    return [{"drama_id": i % 50 + 1, "title": f"T{i % 50}", "language": "ko",
             "spellings": {f"name{i + j}": [f"Name{i + j}", 1] for j in range(spellings_per)},
             "total": spellings_per, "canonical": f"Name{i}", "key": f"nm{i}k", "examples": []}
            for i in range(n)]


def test_the_merge_checks_for_cancel_often(isolated_db):
    calls = []

    def check():
        calls.append(1)
        if len(calls) > 2:
            raise background_jobs.JobCancelled()

    started = time.monotonic()
    with pytest.raises(background_jobs.JobCancelled):
        report._merge(_synthetic_records(5000), check, time.monotonic() + 60)
    assert len(calls) == 3 and time.monotonic() - started < 2


def test_the_merge_stops_at_its_deadline_and_the_report_says_so(isolated_db, monkeypatch):
    _three_titles()
    monkeypatch.setattr(report, "MAX_MERGE_SECONDS", -1)
    result = _scan()
    assert result["stopped_early"] is True and result["truncated"] is True
    monkeypatch.setattr(report, "MAX_MERGE_SECONDS", 30)
    assert _scan()["stopped_early"] is False


def test_a_group_holds_a_bounded_number_of_spellings(isolated_db, monkeypatch):
    monkeypatch.setattr(report, "MAX_HELD_VARIANTS", 5)
    groups, _spent, _stopped = report._merge(
        [{"drama_id": 1, "title": "T", "language": "ko", "key": "same", "canonical": "Name0", "total": 40,
          "spellings": {f"name{j}": [f"Name{j}", 1] for j in range(40)}, "examples": []}],
        lambda: None, time.monotonic() + 60)
    assert len(groups) == 1 and len(groups[0]["variants"]) <= 5
