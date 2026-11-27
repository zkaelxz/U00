"""glossary_inbox_service: names found after a run wait for Add or Ignore. discover_names is faked."""
import pytest

import db
from services import glossary_inbox_service as inbox
from services import glossary_variants_service
from lib.errors import NotFoundError


def _cluster(term, *others, existing=None):
    spellings = [{"text": t, "count": 2, "sources": [], "is_alias": False, "is_term": False} for t in (term, *others)]
    return {"suggested_original": term, "existing_term": existing, "total": 2 * len(spellings),
            "spellings": spellings, "examples": []}


@pytest.fixture
def title(isolated_db, monkeypatch):
    sid = db.get_or_create_series("S")
    did = db.create_drama(title_en="T", series_id=sid, source_language="ja")
    found = {"clusters": [_cluster("ヤソ", "ヤソー"), _cluster("ミカ"), _cluster("サク", existing="咲")]}
    monkeypatch.setattr(glossary_variants_service, "discover_names", lambda _id: found)
    return did, sid


def test_refresh_keeps_only_unknown_names(title):
    did, _ = title
    out = inbox.refresh(did)
    assert [c["suggested_original"] for c in out["items"]] == ["ヤソ", "ミカ"] and out["count"] == 2
    assert inbox.get_inbox(did)["count"] == 2


def test_accept_saves_a_term_with_the_other_spellings_as_aliases(title):
    did, sid = title
    inbox.refresh(did)
    out = inbox.accept(did, "ヤソ", "Yaro")
    term = next(t for t in db.list_glossary_terms(sid) if t["term_original"] == "ヤソ")
    assert term["term_translation"] == "Yaro" and term["aliases"] == "ヤソー"
    assert [c["suggested_original"] for c in out["items"]] == ["ミカ"]


def test_ignored_names_stay_gone_after_the_next_refresh(title):
    did, _ = title
    inbox.refresh(did)
    inbox.reject(did, "ミカ")
    assert [c["suggested_original"] for c in inbox.refresh(did)["items"]] == ["ヤソ"]


def test_unknown_name_is_not_found(title):
    did, _ = title
    inbox.refresh(did)
    with pytest.raises(NotFoundError):
        inbox.accept(did, "ない", "None")


def test_a_failed_scan_does_not_raise_from_the_job_hook(title, monkeypatch):
    did, _ = title
    monkeypatch.setattr(glossary_variants_service, "discover_names", lambda _id: 1 / 0)
    inbox.refresh_quietly(did)


def test_add_refuses_a_name_the_glossary_learned_meanwhile(title):
    from lib.errors import ConflictError
    did, sid = title
    inbox.refresh(did)
    db.upsert_glossary_term(sid, "ヤソ", "Yaro", aliases="ヤソー|ヤソ君")
    with pytest.raises(ConflictError):
        inbox.accept(did, "ヤソ", "Other")
    term = next(t for t in db.list_glossary_terms(sid) if t["term_original"] == "ヤソ")
    assert term["term_translation"] == "Yaro" and "ヤソ君" in term["aliases"]
    assert [c["suggested_original"] for c in inbox.get_inbox(did)["items"]] == ["ミカ"]


def test_malformed_stored_entries_are_dropped(title):
    did, _ = title
    db.update_drama(did, glossary_inbox='{"items": [1, {"suggested_original": "x"}], "rejected": []}')
    assert inbox.get_inbox(did)["count"] == 0


def test_restore_puts_an_ignored_name_back(title):
    did, _ = title
    inbox.refresh(did)
    inbox.reject(did, "ヤソ")
    out = inbox.restore(did, ["ヤソ", "ヤソー"])
    assert [c["suggested_original"] for c in out["items"]] == ["ヤソ", "ミカ"]
