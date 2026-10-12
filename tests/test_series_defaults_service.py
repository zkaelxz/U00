"""services/series_defaults_service.py: a series' default title settings
(validated JSON on the series), inherited by titles created in or moved into
the series, and applied to every title in it after a server-side preview."""

import contextlib
import json

import pytest

import background_jobs
import db
from services import drama_service
from services import series_defaults_service as svc
from services.service_errors import (ConflictError, ForbiddenError, InvalidInputError,
                                     NotFoundError)

LOCAL = {"user_id": None, "is_admin": True, "is_local_owner": True}


def _p(uid, admin=False, override=None):
    return {"user_id": uid, "is_admin": admin, "is_local_owner": False,
            "admin_override": admin if override is None else override}


@pytest.fixture
def people(isolated_db):
    a = db.auth_create_user("a@example.com")
    b = db.auth_create_user("b@example.com")
    adm = db.auth_create_user("admin@example.com", is_admin=True)
    return {"a": _p(a), "b": _p(b), "admin": _p(adm, admin=True),
            "remote_admin": _p(adm, admin=True, override=False), "a_id": a, "b_id": b}


def _audit(action):
    with contextlib.closing(db.get_conn()) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM audit_log WHERE action = ? ORDER BY id", (action,))]


GOOD = {"beam_size": 8, "vad_threshold": 0.4, "whisper_fast_mode": True,
        "include_genre_notes": False, "translate_thinking": True,
        "separation_backend": "demucs"}


# --- validation and storage ------------------------------------------------

class TestDefaults:
    def test_save_and_read_back(self, people):
        s = db.create_series("Saga", owner_user_id=people["a_id"])
        assert svc.get_defaults(people["a"], s)["settings"] == {}
        saved = svc.set_defaults(people["a"], s, GOOD)
        assert saved["settings"] == GOOD
        assert svc.get_defaults(people["a"], s)["settings"] == GOOD
        stored = next(x for x in db.list_series() if x["id"] == s)["default_settings"]
        assert json.loads(stored) == GOOD

    def test_an_empty_dict_clears_them(self, people):
        s = db.create_series("Saga", owner_user_id=people["a_id"])
        svc.set_defaults(people["a"], s, GOOD)
        assert svc.set_defaults(people["a"], s, {})["settings"] == {}

    def test_the_field_list_names_every_setting_and_no_secret(self, people):
        s = db.create_series("Saga", owner_user_id=people["a_id"])
        fields = svc.get_defaults(people["a"], s)["fields"]
        keys = {f["key"] for f in fields}
        assert keys == set(svc.SETTINGS)
        assert not any("key" in k.split("_") or "token" in k or "secret" in k for k in keys)

    @pytest.mark.parametrize("bad", [
        {"api_key": "sk-123"},                       # not a setting
        {"title_en": "renamed"},                     # title data, not a setting
        {"status": "translated"},
        {"beam_size": "8"},                          # wrong type
        {"beam_size": True},                         # a bool is not a number here
        {"beam_size": 99},                           # out of range
        {"vad_threshold": 2.0},
        {"whisper_fast_mode": 1},                    # a number is not a bool
        {"separation_backend": "rm -rf"},            # not one of the choices
        {"translation_engine": "no_such_engine"},
        {"whisper_size": "../../etc"},
        {"min_pause_sec": float("nan")},
    ])
    def test_rejects_unknown_keys_wrong_types_and_bounds(self, people, bad):
        s = db.create_series("Saga", owner_user_id=people["a_id"])
        with pytest.raises(InvalidInputError):
            svc.set_defaults(people["a"], s, bad)
        assert svc.get_defaults(people["a"], s)["settings"] == {}

    def test_rejects_a_non_dict(self, people):
        s = db.create_series("Saga", owner_user_id=people["a_id"])
        for bad in (None, [], "beam_size=8"):
            with pytest.raises(InvalidInputError):
                svc.set_defaults(people["a"], s, bad)

    def test_a_stored_value_that_stopped_being_valid_is_dropped_on_read(self, people):
        s = db.create_series("Saga", owner_user_id=people["a_id"])
        with contextlib.closing(db.get_conn()) as conn:
            conn.execute("UPDATE series SET default_settings = ? WHERE id = ?",
                         (json.dumps({"beam_size": 8, "removed_knob": 1,
                                      "separation_backend": "gone"}), s))
            conn.commit()
        assert svc.get_defaults(people["a"], s)["settings"] == {"beam_size": 8}
        with contextlib.closing(db.get_conn()) as conn:
            conn.execute("UPDATE series SET default_settings = 'not json' WHERE id = ?", (s,))
            conn.commit()
        assert svc.get_defaults(people["a"], s)["settings"] == {}


class TestOwnership:
    def test_other_users_private_series_is_neither_readable_nor_writable(self, people):
        s = db.create_series("Mine", owner_user_id=people["a_id"], is_private=True)
        with pytest.raises(NotFoundError):
            svc.get_defaults(people["b"], s)
        with pytest.raises(NotFoundError):
            svc.set_defaults(people["b"], s, GOOD)
        with pytest.raises(NotFoundError):
            svc.apply_preview(people["b"], s, "fill")
        with pytest.raises(NotFoundError):
            svc.apply_defaults(people["b"], s, "fill")

    def test_a_remote_admin_reads_but_does_not_write(self, people):
        s = db.create_series("Mine", owner_user_id=people["a_id"], is_private=True)
        assert svc.get_defaults(people["remote_admin"], s)["settings"] == {}
        with pytest.raises(ForbiddenError):
            svc.set_defaults(people["remote_admin"], s, GOOD)
        with pytest.raises(ForbiddenError):
            svc.apply_defaults(people["remote_admin"], s, "fill")

    def test_unknown_series(self, people):
        with pytest.raises(NotFoundError):
            svc.get_defaults(people["a"], 9999)


# --- inheritance -----------------------------------------------------------

class TestInheritance:
    def test_a_new_title_in_the_series_starts_with_the_defaults(self, people):
        s = db.create_series("Saga", owner_user_id=people["a_id"])
        svc.set_defaults(people["a"], s, GOOD)
        d = drama_service.create_drama(source_language="ko", title_en="New", series_id=s,
                                       principal=people["a"])
        row = db.get_drama(d["id"])
        assert (row["beam_size"], row["vad_threshold"], row["whisper_fast_mode"]) == (8, 0.4, 1)
        assert (row["include_genre_notes"], row["translate_thinking"]) == (0, 1)
        assert row["separation_backend"] == "demucs"

    def test_a_new_title_naming_an_existing_series_inherits_too(self, people):
        s = db.create_series("Saga", owner_user_id=people["a_id"])
        svc.set_defaults(people["a"], s, {"beam_size": 9})
        d = drama_service.create_drama(source_language="ko", title_en="New",
                                       new_series_name="Saga", principal=people["a"])
        assert db.get_drama(d["id"])["beam_size"] == 9

    def test_a_preset_engine_beats_the_series_engine(self, people):
        s = db.create_series("Saga", owner_user_id=people["a_id"])
        svc.set_defaults(people["a"], s, {"translation_engine": "gemini", "beam_size": 9})
        preset = db.save_preset("P", translation_engine="deepseek")
        d = drama_service.create_drama(source_language="ko", title_en="New", series_id=s,
                                       preset_id=preset, principal=people["a"])
        row = db.get_drama(d["id"])
        assert (row["translation_engine"], row["beam_size"]) == ("deepseek", 9)

    def test_a_title_outside_the_series_is_unaffected(self, people):
        s = db.create_series("Saga", owner_user_id=people["a_id"])
        svc.set_defaults(people["a"], s, GOOD)
        d = drama_service.create_drama(source_language="ko", title_en="Solo",
                                       principal=people["a"])
        assert db.get_drama(d["id"])["beam_size"] == 5

    def test_a_moved_title_keeps_what_it_set_and_fills_the_rest(self, people):
        s = db.create_series("Saga", owner_user_id=people["a_id"])
        svc.set_defaults(people["a"], s, GOOD)
        d = db.create_drama(title_en="Old", source_language="ko", owner_user_id=people["a_id"])
        db.update_drama(d, beam_size=3)             # set by the title itself
        drama_service.update_drama_metadata(d, series_id=s, principal=people["a"])
        row = db.get_drama(d)
        assert row["beam_size"] == 3
        assert row["vad_threshold"] == 0.4 and row["whisper_fast_mode"] == 1

    def test_a_default_wrong_for_the_titles_language_is_skipped(self, people, monkeypatch):
        s = db.create_series("Saga", owner_user_id=people["a_id"])
        monkeypatch.setattr(svc.transcribe_service, "check_whisper_size_for_language",
                            lambda size, lang: (_ for _ in ()).throw(
                                InvalidInputError("not for this language"))
                            if lang == "ja" else None)
        svc.set_defaults(people["a"], s, {"whisper_size": sorted(svc.transcribe_service._allowed_whisper_sizes())[0],
                                          "beam_size": 7})
        d = drama_service.create_drama(source_language="ja", title_en="JP", series_id=s,
                                       principal=people["a"])
        row = db.get_drama(d["id"])
        assert row["beam_size"] == 7 and row["whisper_size"] is None


# --- apply to every title --------------------------------------------------

def _series_with_titles(owner_id):
    s = db.create_series("Saga", owner_user_id=owner_id)
    d1 = db.create_drama(title_en="One", source_language="ko", series_id=s,
                         owner_user_id=owner_id)
    d2 = db.create_drama(title_en="Two", source_language="ko", series_id=s,
                         owner_user_id=owner_id)
    db.update_drama(d2, beam_size=3, whisper_fast_mode=1)
    return s, d1, d2


class TestApply:
    def test_preview_lists_per_title_changes_without_writing(self, people):
        s, d1, d2 = _series_with_titles(people["a_id"])
        svc.set_defaults(people["a"], s, {"beam_size": 8, "whisper_fast_mode": False})
        p = svc.apply_preview(people["a"], s, "fill")
        assert p["title_count"] == 2
        by_id = {t["id"]: t for t in p["titles"]}
        # Fill-empty: d1 has nothing set; d2 set both itself.
        assert set(by_id[d1]["changes"]) == {"beam_size"}
        assert by_id[d2]["changes"] == {}
        assert p["changed_titles"] == 1
        assert db.get_drama(d1)["beam_size"] == 5

        o = svc.apply_preview(people["a"], s, "overwrite")
        by_id = {t["id"]: t for t in o["titles"]}
        assert by_id[d2]["changes"]["beam_size"] == {"from": 3, "to": 8}
        assert by_id[d2]["changes"]["whisper_fast_mode"] == {"from": True, "to": False}
        assert o["changed_titles"] == 2

    def test_fill_empty_apply(self, people):
        s, d1, d2 = _series_with_titles(people["a_id"])
        svc.set_defaults(people["a"], s, {"beam_size": 8})
        r = svc.apply_defaults(people["a"], s, "fill")
        assert (r["titles_changed"], r["fields_changed"]) == (1, 1)
        assert db.get_drama(d1)["beam_size"] == 8
        assert db.get_drama(d2)["beam_size"] == 3

    def test_overwrite_apply_touches_only_settings(self, people):
        s, d1, d2 = _series_with_titles(people["a_id"])
        before = db.get_drama(d2)
        svc.set_defaults(people["a"], s, {"beam_size": 8, "whisper_fast_mode": False})
        r = svc.apply_defaults(people["a"], s, "overwrite")
        assert (r["titles_changed"], r["fields_changed"]) == (2, 3)
        after = db.get_drama(d2)
        assert (after["beam_size"], after["whisper_fast_mode"]) == (8, 0)
        for key in ("title_en", "status", "source_language", "series_id", "owner_user_id",
                    "is_private"):
            assert after[key] == before[key], key

    def test_audit_has_counts_only(self, people):
        s, *_ = _series_with_titles(people["a_id"])
        svc.set_defaults(people["a"], s, {"beam_size": 8})
        svc.apply_defaults(people["a"], s, "overwrite")
        rows = _audit("series.apply_defaults")
        assert len(rows) == 1
        detail = rows[0]["detail_redacted"]
        assert "titles=2" in detail and "Saga" not in detail and "beam_size" not in detail

    def test_unknown_mode_is_refused(self, people):
        s, *_ = _series_with_titles(people["a_id"])
        with pytest.raises(InvalidInputError):
            svc.apply_preview(people["a"], s, "everything")

    def test_refused_while_a_job_runs_for_any_title(self, people, monkeypatch):
        s, d1, d2 = _series_with_titles(people["a_id"])
        svc.set_defaults(people["a"], s, {"beam_size": 8})
        monkeypatch.setattr(background_jobs, "any_job_running_for_drama",
                            lambda drama_id, **_kw: drama_id == d2)
        assert svc.apply_preview(people["a"], s, "fill")["job_running"] is True
        with pytest.raises(ConflictError):
            svc.apply_defaults(people["a"], s, "fill")
        assert db.get_drama(d1)["beam_size"] == 5

    def test_a_title_changed_after_the_plan_refuses_the_whole_apply(self, people, monkeypatch):
        s, d1, d2 = _series_with_titles(people["a_id"])
        svc.set_defaults(people["a"], s, {"beam_size": 8})

        def late_edit(_series_id):   # runs between the plan and the write
            db.update_drama(d1, beam_size=4)
            return False
        monkeypatch.setattr(svc, "_job_running", late_edit)
        with pytest.raises(ConflictError):
            svc.apply_defaults(people["a"], s, "overwrite")
        assert db.get_drama(d2)["beam_size"] == 3          # nothing written
        assert db.get_drama(d1)["beam_size"] == 4

    def test_nothing_set_means_nothing_to_apply(self, people):
        s, *_ = _series_with_titles(people["a_id"])
        r = svc.apply_defaults(people["a"], s, "overwrite")
        assert (r["titles_changed"], r["fields_changed"]) == (0, 0)


def test_the_db_write_refuses_a_key_outside_the_whitelist(isolated_db):
    s = db.create_series("S")
    d = db.create_drama(title_en="T", source_language="ko", series_id=s)
    row = db.get_drama(d)
    plan = {d: {"old": {"status": row["status"]}, "new": {"status": "exported"}}}
    with pytest.raises(ValueError):
        db.apply_series_settings(s, plan, allowed=frozenset(svc.SETTINGS))
    assert db.get_drama(d)["status"] == row["status"]
