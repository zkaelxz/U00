"""
A learned style profile is distilled from every edit its learner could
see, so it reaches only those who see everything (auth off, the PC owner,
admins; owner decision 2026-10-14) and the member who learned it, whose
profile is built only from edits they could see: never another member,
neither in the style view nor in their translate prompts.
Fully mocked: no network.
"""

import pytest

import adaptive_style
import background_jobs
import core
import db
import line_tools
import translate_engines
from core import Line
from services import (library_admin_service, line_ai_service, ownership_service,
                      review_extras_service, review_jobs_service, review_records_service,
                      translate_run_service, translate_service, workspace_job_service)
from services import workspace_review_job_service as wrjs
from services.auth_service import PERMISSIONS
from services.service_errors import ConflictError, NotFoundError
from tests.job_helpers import wait_for_job

PREF = "Always use contractions in dialogue"
MINE = "Keep honorifics"


def _principal(uid, admin=False):
    return {"user_id": uid, "is_admin": admin, "is_local_owner": False,
            "admin_override": False, "permissions": list(PERMISSIONS)}


@pytest.fixture
def people(isolated_db):
    admin = _principal(db.auth_create_user("admin@example.com"), admin=True)
    member = _principal(db.auth_create_user("member@example.com"))
    other = _principal(db.auth_create_user("other@example.com"))
    return admin, member, other


def _shared_title(learned_by=None, status="translated"):
    """A title in a shared series whose stored profile someone else learned
    (learned_by None: learned at the PC, or before profiles named a learner)."""
    sid = db.get_or_create_series("Shared")
    did = db.create_drama(title_zh="D", series_id=sid, status=status,
                          translation_engine="claude")
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="", flag="bad"),
                        Line(idx=1, start=1, end=2, zh="再见", en="bye")])
    for i in range(adaptive_style.MIN_SAMPLES_TO_LEARN):
        db.record_edit_sample(did, f"中{i}", f"AI {i}", f"Edit {i}")
    profile = {"preferences": [PREF], "summary": "Theirs"}
    if learned_by is not None:
        profile["learned_by"] = learned_by
    db.replace_style_profile(f"series:{sid}", {"preferences": ["older"], "summary": "Old",
                                               **profile}, 8)
    db.replace_style_profile(f"series:{sid}", profile, 8)
    return did


def _guidelines(did, principal):
    return workspace_job_service.build_run_style_context(
        did, db.get_drama(did), [Line(idx=0, start=0, end=1, zh="你好")], "audio_drama",
        principal=principal)[1]


@pytest.fixture
def acting(people):
    """Runs the service call as if inside an API request by `principal`."""
    tokens = []

    def bind(principal):
        tokens.append(ownership_service.bind_request())
        ownership_service.note_acting_principal(principal)
    yield bind
    for token in reversed(tokens):
        ownership_service.unbind_request(token)



@pytest.mark.parametrize("learner", ["pc", "admin"])
def test_member_style_view_hides_a_profile_someone_else_learned(people, learner):
    admin, member, _ = people
    did = _shared_title(None if learner == "pc" else admin["user_id"])
    view = review_extras_service.get_style(did, principal=member)
    assert view["profile"] is None and view["history"] == []
    # The counts and the learn action stay as they were for someone who may edit.
    assert view["edit_count"] == adaptive_style.MIN_SAMPLES_TO_LEARN
    for sees_all in (None, admin):
        view = review_extras_service.get_style(did, principal=sees_all)
        assert view["profile"]["preferences"] == [PREF] and len(view["history"]) == 1


def test_member_tendencies_leave_out_someone_elses_profile(people):
    admin, member, _ = people
    did = _shared_title(admin["user_id"])
    assert review_records_service.get_edit_tendencies(did, principal=member)["profile"] is None
    assert review_records_service.get_edit_tendencies(
        did, principal=admin)["profile"]["preferences"] == [PREF]


def test_member_translate_prompt_leaves_out_someone_elses_profile(people):
    admin, member, _ = people
    did = _shared_title(admin["user_id"])
    assert PREF not in _guidelines(did, member)
    assert PREF in _guidelines(did, admin)
    assert PREF in _guidelines(did, None)


def test_member_translate_run_leaves_out_someone_elses_profile(people, acting, monkeypatch):
    admin, member, _ = people
    seen = []
    real = translate_run_service.translation_guide.build_style_guidelines

    def spy(*a, **k):
        seen.append(real(*a, **k))
        return seen[-1]
    monkeypatch.setattr(translate_run_service.translation_guide, "build_style_guidelines", spy)
    did = _shared_title(admin["user_id"], status="aligned")
    acting(member)
    wait_for_job(translate_run_service.start_translate_run(did, engine_name="fake")["job_id"])
    assert seen and PREF not in seen[0]


def test_member_line_improve_leaves_out_someone_elses_profile(people, acting, monkeypatch):
    admin, member, _ = people

    class FakeEngine:
        model = "fake-model"
        supports_reference = True
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, env_path=None: "k")
    monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: FakeEngine())
    seen = {}

    def fake(zh, en, engine, issue="", source_language="zh", style_guidelines="", kind="subtitle"):
        seen["style"] = style_guidelines
        return "hi"
    monkeypatch.setattr(line_tools, "improve_line", fake)
    did = _shared_title(admin["user_id"])
    acting(member)
    line_ai_service.improve_line(did, db.load_lines(did)[1]["id"], engine_name="claude")
    assert PREF not in seen["style"]


def test_fix_flagged_job_uses_its_starters_view(people, acting, monkeypatch):
    admin, member, _ = people
    did = _shared_title(admin["user_id"])
    started = {}
    monkeypatch.setattr(review_jobs_service.job_launch, "start_job",
                        lambda job_id, target, *a, **k: started.update(k) or True)
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda *a, **k: "k")
    acting(member)
    review_jobs_service.start_fix_flagged(did, engine_name="fake")
    assert started["principal"] == member

    seen = []
    real = wrjs.build_run_style_context

    def spy(*a, **k):
        out = real(*a, **k)
        seen.append(out[1])
        return out
    monkeypatch.setattr(wrjs, "build_run_style_context", spy)

    class Engine:
        name = "fake"

        def translate_batch(self, zh, context):
            return ["ok"]
    background_jobs.clear_job("fix_s3")
    wrjs.run_fix_flagged_lines_job("fix_s3", did, core.lines_from_rows(db.load_lines(did)),
                                   None, "small", False, "zh", Engine(), "fake",
                                   principal=started["principal"])
    background_jobs.clear_job("fix_s3")
    assert seen and PREF not in seen[0]


def test_bulk_translate_job_uses_its_starters_view(people, monkeypatch):
    admin, member, _ = people
    did = _shared_title(admin["user_id"], status="aligned")
    started = {}
    monkeypatch.setattr(library_admin_service.job_launch, "start_job",
                        lambda job_id, target, *a, **k: started.update(k) or True)
    monkeypatch.setattr(library_admin_service.paid_job_service, "require_monthly_cap",
                        lambda engines: 50.0)
    library_admin_service.start_bulk_translate([did], principal=member)
    assert started["principal"] == member

    seen = []
    real = workspace_job_service.build_run_style_context

    def spy(*a, **k):
        out = real(*a, **k)
        seen.append(out[1])
        return out
    monkeypatch.setattr(workspace_job_service, "build_run_style_context", spy)
    monkeypatch.setattr(workspace_job_service.translate_engines, "get_engine",
                        lambda *a, **k: object())
    monkeypatch.setattr(workspace_job_service.job_launch, "start_job", lambda *a, **k: False)
    monkeypatch.setattr(workspace_job_service.db, "get_month_spend", lambda: 0.0)
    workspace_job_service.run_bulk_series_translate_job(
        "bulk_s3", [did], {"claude": "k"}, monthly_cap=50.0, principal=started["principal"])
    background_jobs.clear_job("bulk_s3")
    assert seen and PREF not in seen[0]


@pytest.fixture
def learner(monkeypatch):
    """learn_style's engine and LLM call, faked; returns the analyze calls."""
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda _n: "k")

    class Engine:
        supports_reference = True
        model = "fake-model"
    monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: Engine())
    calls = []

    def analyze(samples, engine, existing_profile=None, usage_cb=None):
        calls.append(existing_profile)
        return {"preferences": [MINE], "summary": "Mine", "confidence": "high"}
    monkeypatch.setattr(adaptive_style, "analyze_edit_patterns", analyze)
    return calls


def _scope_of(did):
    return f"series:{db.get_drama(did)['series_id']}"


def test_member_learn_never_replaces_a_profile_they_cannot_see(people, learner):
    admin, member, _ = people
    did = _shared_title(admin["user_id"])
    with pytest.raises(ConflictError):
        review_extras_service.learn_style(did, principal=member)
    assert learner == []  # refused before the LLM call
    stored = db.get_style_profile(_scope_of(did))["profile"]
    assert stored["preferences"] == [PREF] and stored["learned_by"] == admin["user_id"]


def test_member_learns_their_own_profile(people, learner):
    admin, member, other = people
    did = _shared_title(admin["user_id"])
    db.replace_style_profile(_scope_of(did), {"preferences": []}, 0)  # reset at the PC
    view = review_extras_service.learn_style(did, principal=member)
    assert view["profile"]["preferences"] == [MINE]
    assert view["history"] == []  # the replaced profiles were someone else's
    assert MINE in _guidelines(did, member)
    assert review_extras_service.get_style(did, principal=other)["profile"] is None
    assert MINE not in _guidelines(did, other)
    assert review_extras_service.get_style(did, principal=admin)["profile"]["preferences"] == [MINE]


def test_member_relearn_keeps_an_admins_pause(people, learner, acting, monkeypatch):
    admin, member, _ = people
    did = _shared_title(member["user_id"], status="aligned")
    review_extras_service.set_style_applied(did, False, principal=admin)
    review_extras_service.learn_style(did, principal=member)
    stored = db.get_style_profile(_scope_of(did))["profile"]
    assert stored["preferences"] == [MINE] and stored["apply"] is False
    seen = []
    real = translate_run_service.translation_guide.build_style_guidelines

    def spy(*a, **k):
        seen.append(real(*a, **k))
        return seen[-1]
    monkeypatch.setattr(translate_run_service.translation_guide, "build_style_guidelines", spy)
    acting(admin)
    wait_for_job(translate_run_service.start_translate_run(did, engine_name="fake")["job_id"])
    assert seen and MINE not in seen[0]


def test_member_cannot_pause_a_profile_they_cannot_see(people):
    admin, member, _ = people
    did = _shared_title(admin["user_id"])
    with pytest.raises(NotFoundError):
        review_extras_service.set_style_applied(did, False, principal=member)
    assert "apply" not in db.get_style_profile(
        f"series:{db.get_drama(did)['series_id']}")["profile"]
    assert review_extras_service.set_style_applied(
        did, False, principal=admin)["profile"]["applied"] is False
