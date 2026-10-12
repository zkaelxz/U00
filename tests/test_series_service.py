"""services/series_service.py: the Series page's rename, delete and
copy-terms-between-series actions, with their ownership rules."""

import contextlib

import pytest

import background_jobs
import db
from core import Line
from services import series_service as svc
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


# --- page --------------------------------------------------------------------

class TestPage:
    def test_lists_titles_counts_and_line_counts(self, people):
        s = db.create_series("Saga", owner_user_id=people["a_id"])
        d1 = db.create_drama(title_en="One", source_language="zh", series_id=s,
                             owner_user_id=people["a_id"], media_type="novel")
        db.create_drama(title_en="Two", source_language="zh", series_id=s,
                        owner_user_id=people["a_id"])
        db.create_drama(title_en="Elsewhere", source_language="zh")
        db.save_lines(d1, [Line(idx=0, start=0, end=1, zh="一"), Line(idx=1, start=1, end=2, zh="二")])
        db.upsert_glossary_term(s, "林", "Lin")
        db.update_series_instructions(s, "Keep it formal.")

        page = svc.get_series_page(people["a"], s)

        assert page["name"] == "Saga"
        assert page["owned_by_me"] is True
        assert page["glossary_term_count"] == 1
        assert page["has_instructions"] is True
        titles = {t["title_en"]: t for t in page["titles"]}
        assert set(titles) == {"One", "Two"}
        assert titles["One"]["line_count"] == 2
        assert titles["One"]["media_type"] == "novel"
        assert titles["Two"]["line_count"] == 0

    def test_other_users_private_series_is_not_found(self, people):
        s = db.create_series("Mine", owner_user_id=people["a_id"], is_private=True)
        with pytest.raises(NotFoundError):
            svc.get_series_page(people["b"], s)
        assert svc.get_series_page(people["admin"], s)["name"] == "Mine"

    def test_list_includes_single_title_and_empty_series(self, people):
        one = db.create_series("One title", owner_user_id=people["a_id"])
        db.create_drama(title_en="Solo", source_language="zh", series_id=one,
                        owner_user_id=people["a_id"])
        empty = db.create_series("Empty", owner_user_id=people["a_id"])
        hidden = db.create_series("Hidden", owner_user_id=people["b_id"], is_private=True)
        ids = {s["id"]: s for s in svc.list_series(people["a"])}
        assert one in ids and empty in ids and hidden not in ids
        assert ids[one]["title_count"] == 1


# --- rename ------------------------------------------------------------------

class TestRename:
    def test_trims_and_renames(self, people):
        s = db.create_series("Old", owner_user_id=people["a_id"])
        assert svc.rename_series(people["a"], s, "  New name ")["name"] == "New name"
        assert svc.get_series_page(people["a"], s)["name"] == "New name"

    def test_case_only_change_of_its_own_name_is_allowed(self, people):
        s = db.create_series("saga", owner_user_id=people["a_id"])
        assert svc.rename_series(people["a"], s, "Saga")["name"] == "Saga"

    @pytest.mark.parametrize("bad", ["", "   ", "x" * 301, "bad\nname", 7])
    def test_rejects_blank_long_or_control_characters(self, people, bad):
        s = db.create_series("Old", owner_user_id=people["a_id"])
        with pytest.raises(InvalidInputError):
            svc.rename_series(people["a"], s, bad)

    def test_name_clash_ignores_case_and_width(self, people):
        db.create_series("Saga", owner_user_id=people["a_id"])
        s = db.create_series("Other", owner_user_id=people["a_id"])
        for clash in ("saga", " SAGA ", "Ｓａｇａ"):
            with pytest.raises(ConflictError):
                svc.rename_series(people["a"], s, clash)
        assert svc.get_series_page(people["a"], s)["name"] == "Other"

    def test_exact_name_of_a_series_the_caller_cannot_see_is_refused(self, people):
        db.create_series("Secret", owner_user_id=people["b_id"], is_private=True)
        s = db.create_series("Mine", owner_user_id=people["a_id"])
        with pytest.raises(ConflictError):
            svc.rename_series(people["a"], s, "Secret")

    def test_private_series_only_by_owner_or_admin(self, people):
        s = db.create_series("Private", owner_user_id=people["a_id"], is_private=True)
        with pytest.raises(NotFoundError):
            svc.rename_series(people["b"], s, "Taken over")
        with pytest.raises(ForbiddenError):
            svc.rename_series(people["remote_admin"], s, "Taken over")
        assert svc.rename_series(people["admin"], s, "Renamed by admin")["name"] == \
            "Renamed by admin"

    def test_unknown_series_is_not_found(self, people):
        with pytest.raises(NotFoundError):
            svc.rename_series(people["a"], 9999, "x")


# --- delete ------------------------------------------------------------------

def _full_series(owner_id, private=False):
    s = db.create_series("Full", owner_user_id=owner_id, is_private=private)
    d1 = db.create_drama(title_en="Ep1", source_language="zh", series_id=s,
                         owner_user_id=owner_id)
    d2 = db.create_drama(title_en="Ep2", source_language="zh", series_id=s,
                         owner_user_id=owner_id)
    db.upsert_glossary_term(s, "林", "Lin")
    db.upsert_glossary_term(s, "苏", "Su")
    sc = db.insert_series_character(s, "Lin Feng")
    db.update_series_instructions(s, "Formal.")
    db.add_glossary_dismissals(s, ["废物"])
    db.record_translation_memory(s, "你好", "Hello")
    return s, d1, d2, sc


class TestDelete:
    def test_preview_counts_everything_it_would_delete(self, people):
        s, *_ = _full_series(people["a_id"])
        p = svc.delete_preview(people["a"], s)
        assert p["title_count"] == 2
        assert p["glossary_term_count"] == 2
        assert p["character_count"] == 1
        assert p["has_instructions"] is True
        assert p["dismissal_count"] == 1
        assert p["translation_memory_count"] == 1
        assert p["needs_typed_confirm"] is True
        assert p["job_running"] is False

    def test_keeps_titles_as_no_series_and_deletes_series_data(self, people):
        s, d1, d2, sc = _full_series(people["a_id"])
        ch = db.create_drama(title_en="Linked", source_language="zh")
        db.upsert_character(ch, "SPEAKER_00", character_name="Lin Feng", series_character_id=sc)

        r = svc.delete_series(people["a"], s, confirm=True, confirm_text="DELETE")

        assert r["deleted"] is True and r["titles_kept"] == 2
        for d in (d1, d2):
            row = db.get_drama(d)
            assert row is not None and row["series_id"] is None
        assert db.get_item_ownership("series", s) is None
        assert db.list_glossary_terms(s) == []
        assert db.list_series_characters(s) == []
        assert db.list_glossary_dismissals(s) == []
        assert db.list_translation_memory(s) == []
        kept = [c for c in db.list_characters(ch) if c["speaker_label"] == "SPEAKER_00"]
        assert kept[0]["character_name"] == "Lin Feng"
        assert kept[0]["series_character_id"] is None

    def test_audit_entry_has_counts_only(self, people):
        s, *_ = _full_series(people["a_id"])
        svc.delete_series(people["a"], s, confirm=True, confirm_text="DELETE")
        rows = _audit("series.delete")
        assert len(rows) == 1
        detail = rows[0]["detail_redacted"]
        assert "Full" not in detail and "林" not in detail
        assert "titles=2" in detail and "terms=2" in detail

    def test_titles_leaving_a_private_series_stay_private(self, people):
        s, d1, _d2, _sc = _full_series(people["a_id"], private=True)
        svc.delete_series(people["a"], s, confirm=True, confirm_text="DELETE")
        assert db.get_item_ownership("drama", d1)["is_private"] == 1

    @pytest.mark.parametrize("confirm,text", [(False, "DELETE"), (True, ""), (True, "delete")])
    def test_holding_anything_needs_typed_delete(self, people, confirm, text):
        s, *_ = _full_series(people["a_id"])
        with pytest.raises(InvalidInputError):
            svc.delete_series(people["a"], s, confirm=confirm, confirm_text=text)
        assert db.get_item_ownership("series", s) is not None

    def test_empty_series_needs_only_confirm(self, people):
        s = db.create_series("Empty", owner_user_id=people["a_id"])
        assert svc.delete_preview(people["a"], s)["needs_typed_confirm"] is False
        with pytest.raises(InvalidInputError):
            svc.delete_series(people["a"], s, confirm=False)
        assert svc.delete_series(people["a"], s, confirm=True)["deleted"] is True
        assert db.get_item_ownership("series", s) is None

    def test_refused_while_a_job_runs_for_one_of_its_titles(self, people, monkeypatch):
        s, _d1, d2, _sc = _full_series(people["a_id"])
        monkeypatch.setattr(background_jobs, "any_job_running_for_drama",
                            lambda drama_id, **_kw: drama_id == d2)
        assert svc.delete_preview(people["a"], s)["job_running"] is True
        with pytest.raises(ConflictError):
            svc.delete_series(people["a"], s, confirm=True, confirm_text="DELETE")
        assert db.get_item_ownership("series", s) is not None
        assert db.get_drama(d2)["series_id"] == s

    def test_other_users_private_series_is_refused(self, people):
        s, *_ = _full_series(people["a_id"], private=True)
        with pytest.raises(NotFoundError):
            svc.delete_preview(people["b"], s)
        with pytest.raises(NotFoundError):
            svc.delete_series(people["b"], s, confirm=True, confirm_text="DELETE")
        assert db.get_item_ownership("series", s) is not None

    def test_only_the_owner_or_an_admin_deletes_a_shared_series(self, people):
        s, *_ = _full_series(people["a_id"])
        with pytest.raises(ForbiddenError):
            svc.delete_series(people["b"], s, confirm=True, confirm_text="DELETE")
        with pytest.raises(ForbiddenError):
            svc.delete_series(people["remote_admin"], s, confirm=True, confirm_text="DELETE")
        assert svc.delete_series(LOCAL, s, confirm=True, confirm_text="DELETE")["deleted"]

    def test_no_files_are_touched(self, people, tmp_path):
        import os
        s, d1, *_ = _full_series(people["a_id"])
        folder = os.path.join(db.DRAMAS_DIR, str(d1))
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "keep.txt"), "w") as f:
            f.write("x")
        svc.delete_series(people["a"], s, confirm=True, confirm_text="DELETE")
        assert os.path.exists(os.path.join(folder, "keep.txt"))


# --- copy glossary terms -----------------------------------------------------

class TestCopyTerms:
    def _pair(self, people):
        src = db.create_series("Source", owner_user_id=people["a_id"])
        dst = db.create_series("Dest", owner_user_id=people["a_id"])
        db.upsert_glossary_term(src, "林峰", "Lin Feng", notes="hero", category="person_name")
        db.upsert_glossary_term(src, "Ｓｅｃｔ ", "Sect from source")
        db.upsert_glossary_term(src, "青云", "Qingyun")
        db.upsert_glossary_term(dst, "sect", "Dest's sect", notes="keep me")
        return src, dst

    def test_preview_counts_new_and_skipped_without_writing(self, people):
        src, dst = self._pair(people)
        p = svc.copy_terms_preview(people["a"], dst, src)
        assert p["new_count"] == 2 and p["skipped_count"] == 1
        assert set(p["new_terms"]) == {"林峰", "青云"}
        assert p["skipped_terms"] == ["Ｓｅｃｔ "]
        assert len(db.list_glossary_terms(dst)) == 1

    def test_apply_adds_new_terms_and_never_overwrites(self, people):
        src, dst = self._pair(people)
        r = svc.copy_terms(people["a"], dst, src)
        assert (r["added"], r["skipped"]) == (2, 1)
        terms = {t["term_original"]: t for t in db.list_glossary_terms(dst)}
        assert set(terms) == {"sect", "林峰", "青云"}
        assert terms["sect"]["term_translation"] == "Dest's sect"
        assert terms["sect"]["notes"] == "keep me"
        assert terms["林峰"]["term_translation"] == "Lin Feng"
        assert terms["林峰"]["category"] == "person_name"
        assert len(db.list_glossary_terms(src)) == 3  # source untouched

    def test_second_apply_adds_nothing(self, people):
        src, dst = self._pair(people)
        svc.copy_terms(people["a"], dst, src)
        r = svc.copy_terms(people["a"], dst, src)
        assert (r["added"], r["skipped"]) == (0, 3)

    def test_source_duplicates_after_normalising_copy_once(self, people):
        src = db.create_series("S", owner_user_id=people["a_id"])
        dst = db.create_series("D", owner_user_id=people["a_id"])
        db.upsert_glossary_term(src, "Sect", "One")
        db.upsert_glossary_term(src, "sect", "Two")
        assert svc.copy_terms(people["a"], dst, src)["added"] == 1
        assert len(db.list_glossary_terms(dst)) == 1

    def test_aliases_and_banned_translations_are_copied(self, people):
        src = db.create_series("S", owner_user_id=people["a_id"])
        dst = db.create_series("D", owner_user_id=people["a_id"])
        db.upsert_glossary_term(src, "林", "Lin", aliases="Lyn", banned_translations="Forest",
                                enforce_exact=True)
        svc.copy_terms(people["a"], dst, src)
        t = db.list_glossary_terms(dst)[0]
        assert (t["aliases"], t["banned_translations"], t["enforce_exact"]) == \
            ("Lyn", "Forest", 1)

    def test_private_source_of_another_user_is_refused(self, people):
        src = db.create_series("Theirs", owner_user_id=people["b_id"], is_private=True)
        db.upsert_glossary_term(src, "秘", "Secret")
        dst = db.create_series("Mine", owner_user_id=people["a_id"])
        for who in ("a", "admin"):
            with pytest.raises(NotFoundError):
                svc.copy_terms_preview(people[who], dst, src)
            with pytest.raises(NotFoundError):
                svc.copy_terms(people[who], dst, src)
        assert db.list_glossary_terms(dst) == []

    def test_destination_must_be_editable(self, people):
        src = db.create_series("Mine", owner_user_id=people["b_id"])
        dst = db.create_series("Theirs", owner_user_id=people["a_id"], is_private=True)
        with pytest.raises(NotFoundError):
            svc.copy_terms(people["b"], dst, src)
        with pytest.raises(ForbiddenError):
            svc.copy_terms(people["remote_admin"], dst, src)

    def test_same_series_is_refused(self, people):
        s = db.create_series("S", owner_user_id=people["a_id"])
        with pytest.raises(InvalidInputError):
            svc.copy_terms_preview(people["a"], s, s)
