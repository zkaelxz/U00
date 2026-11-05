"""address_terms_service: counting common forms of address and saving renderings. Fake lines, offline."""
import pytest

import db
from core import Line
from services import address_terms_service as at, glossary_service
from lib.errors import InvalidInputError


def _title(texts, language="ja", terms=()):
    sid = db.get_or_create_series("S")
    did = db.create_drama(title_en="T", series_id=sid, source_language=language)
    db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=t, en="") for i, t in enumerate(texts)])
    for original, aliases in terms:
        db.upsert_glossary_term(sid, original, "X", aliases="|".join(aliases))
    return did


def _found(out):
    return {t["term"]: t for t in out["terms"]}


def test_japanese_suffix_needs_a_name_before_it(isolated_db):
    did = _title(["田中さんが来た", "ミカさん、行こう", "たくさん食べた", "たくさんある", "お兄ちゃん!", "お兄ちゃんと"])
    found = _found(at.find_address_terms(did))
    assert found["さん"]["count"] == 2            # たくさん is not an address
    assert found["お兄ちゃん"]["count"] == 2
    assert found["さん"]["examples"][0]["line_idx"] == 0 and "田中さん" in found["さん"]["examples"][0]["snippet"]


def test_longest_form_wins_and_single_mentions_are_dropped(isolated_db):
    did = _title(["お兄さんだ", "お兄さんです", "兄さん一人"])
    found = _found(at.find_address_terms(did))
    assert found["お兄さん"]["count"] == 2
    assert "兄さん" not in found and "さん" not in found      # one mention, or part of a longer form


def test_korean_words_respect_word_boundaries(isolated_db):
    did = _title(["형사가 왔다", "형사님", "오빠는 간다", "오빠한테 말해", "김씨 왔어", "이씨 갔어", "씨발"], language="ko")
    found = _found(at.find_address_terms(did))
    assert "형" not in found                       # 형사 is a detective
    assert found["오빠"]["count"] == 2
    assert found["씨"]["count"] == 2               # 씨발 is not an address


def test_chinese_words_and_overlap(isolated_db):
    did = _title(["姐姐你好", "大姐姐来了", "老师好", "老师再见", "姐姐"], language="zh")
    found = _found(at.find_address_terms(did))
    assert found["姐姐"]["count"] == 2 and found["老师"]["count"] == 2
    assert "大姐" not in found      # heard once, below the minimum


def test_terms_already_in_the_glossary_or_aliases_are_left_out(isolated_db):
    did = _title(["姐姐好", "姐姐呢", "老师好", "老师呢", "先生好", "先生呢"], language="zh",
                 terms=[("姐姐", []), ("X", ["老师"])])
    assert set(_found(at.find_address_terms(did))) == {"先生"}


def test_unsupported_language_is_unavailable(isolated_db):
    out = at.find_address_terms(_title(["hello"], language="en"))
    assert out["available"] is False and out["terms"] == []


def test_scan_writes_nothing(isolated_db):
    did = _title(["姐姐好", "姐姐呢"], language="zh")
    at.find_address_terms(did)
    assert glossary_service.list_glossary_terms(did) == []


def test_save_adds_honorific_terms_with_the_chosen_rendering(isolated_db):
    did = _title(["姐姐好"], language="zh")
    out = at.save_address_terms(did, [{"term": "姐姐", "translation": "big sister"},
                                      {"term": "老师", "keep_as_is": True}])
    assert out == {"saved": ["姐姐", "老师"], "skipped_existing": []}
    terms = {t["term_original"]: t for t in glossary_service.list_glossary_terms(did)}
    assert terms["姐姐"]["term_translation"] == "big sister"
    assert terms["姐姐"]["category"] == "honorific" and terms["姐姐"]["policy"] == "translate_meaning"
    assert terms["老师"]["term_translation"] == "老师" and terms["老师"]["policy"] == "keep_pinyin"


def test_save_skips_an_existing_term_without_touching_it(isolated_db):
    did = _title(["x"], language="zh", terms=[("姐姐", [])])
    out = at.save_address_terms(did, [{"term": "姐姐", "translation": "sis"}])
    assert out == {"saved": [], "skipped_existing": ["姐姐"]}
    assert glossary_service.list_glossary_terms(did)[0]["term_translation"] == "X"


@pytest.mark.parametrize("bad", [
    {"term": "さん", "translation": "san"},        # not a Chinese address term
    {"term": "姐姐", "translation": ""},           # a rendering is required
    {"term": "姐姐", "translation": "x" * 201},
])
def test_save_refuses_a_bad_choice_and_writes_nothing(isolated_db, bad):
    did = _title(["x"], language="zh")
    with pytest.raises(InvalidInputError):
        at.save_address_terms(did, [{"term": "老师", "translation": "teacher"}, bad])
    assert glossary_service.list_glossary_terms(did) == []


def test_save_needs_at_least_one_choice(isolated_db):
    with pytest.raises(InvalidInputError):
        at.save_address_terms(_title(["x"], language="zh"), [])
