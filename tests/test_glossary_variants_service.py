"""glossary_variants_service: other spellings of a term, and name clusters. Fake lines, offline."""
import importlib.util

import pytest

import db
import glossary_phonetic as ph
from core import Line
from services import glossary_variants_service as gv
from lib.errors import InvalidInputError, NotFoundError

needs_kakasi = pytest.mark.skipif(importlib.util.find_spec("pykakasi") is None,
                                  reason="pykakasi not installed")
needs_pypinyin = pytest.mark.skipif(importlib.util.find_spec("pypinyin") is None,
                                    reason="pypinyin not installed")


def _title(texts, language="ja", speakers=None, terms=()):
    sid = db.get_or_create_series("S")
    did = db.create_drama(title_en="T", series_id=sid, source_language=language)
    lines = [Line(idx=i, start=i, end=i + 1, zh=t, en="", speaker=(speakers or {}).get(i))
             for i, t in enumerate(texts)]
    db.save_lines(did, lines)
    for original, aliases in terms:
        db.upsert_glossary_term(sid, original, "X", aliases="|".join(aliases))
    return did


@needs_kakasi
class TestFindVariantsJapanese:
    TEXTS = ["ナローが来た", "ナロックはどこだ", "ナロ、行こう", "ナローさん", "今日は晴れだ", "ミカが笑う"]

    def test_finds_sound_alikes_but_not_the_term_or_other_names(self, isolated_db):
        did = _title(self.TEXTS)
        out = gv.find_variants(did, "ナロ")
        found = {c["text"]: c for c in out["candidates"]}
        assert out["available"] and out["term"] == "ナロ"
        assert "ナロー" in found and found["ナロー"]["count"] == 2     # same sound, two lines
        assert "ナロ" not in found                                     # never the term itself
        assert not any("ミカ" in t or "晴" in t for t in found)
        assert found["ナロー"]["examples"][0]["line_idx"] == 0
        assert "ナロー" in found["ナロー"]["examples"][0]["snippet"]

    def test_an_exact_mention_hides_lesser_overlaps(self, isolated_db):
        # ナロ + が / ナロ + 、 are only the term followed by something: not offered.
        out = gv.find_variants(_title(["ナロが来た", "ナロ、行こう"]), "ナロ")
        assert out["candidates"] == []

    def test_existing_alias_is_marked_not_offered_as_new(self, isolated_db):
        did = _title(self.TEXTS, terms=[("ナロ", ["ナロー"])])
        out = gv.find_variants(did, "ナロ")
        found = {c["text"]: c for c in out["candidates"]}
        assert found["ナロー"]["is_alias"] is True
        assert found["ナロー"]["count"] == 2
        assert all(not c["is_alias"] for c in out["candidates"] if c["text"] != "ナロー")

    def test_term_id_and_other_terms(self, isolated_db):
        did = _title(self.TEXTS, terms=[("ナロ", []), ("ナロー", [])])
        term_id = next(t["id"] for t in db.list_glossary_terms(db.get_drama(did)["series_id"])
                       if t["term_original"] == "ナロ")
        out = gv.find_variants(did, term_id)
        assert out["term"] == "ナロ"
        assert {c["text"]: c["is_term"] for c in out["candidates"]}["ナロー"] is True
        with pytest.raises(NotFoundError):
            gv.find_variants(did, 99999)

    def test_results_are_capped_and_ranked(self, isolated_db, monkeypatch):
        monkeypatch.setattr(gv, "MAX_VARIANT_RESULTS", 2)
        texts = ["サクラ", "サクラ", "サクラ", "ザクラ", "サクロ", "タクラ", "サクラコ"]
        out = gv.find_variants(_title(texts), "サクラ")
        assert len(out["candidates"]) <= 2
        scores = [c["score"] for c in out["candidates"]]
        assert scores == sorted(scores, reverse=True)

    def test_scan_budget_is_respected(self, isolated_db, monkeypatch):
        monkeypatch.setattr(gv, "SCAN_MAX_LINES", 3)
        did = _title(["あ"] * 2 + ["ナロー"] * 10)
        out = gv.find_variants(did, "ナロ")
        assert out["truncated"] is True and out["lines_scanned"] == 3
        assert sum(c["count"] for c in out["candidates"]) == 1       # only the third line was read
        monkeypatch.setattr(gv, "SCAN_MAX_LINES", 5000)
        monkeypatch.setattr(gv, "SCAN_MAX_CHARS", 10)
        out = gv.find_variants(did, "ナロ")
        assert out["truncated"] is True and out["lines_scanned"] < 12

    def test_sound_effect_lines_are_not_scanned(self, isolated_db):
        did = _title(["ナローが来た"])
        lines = db.load_line_objects(did)
        lines[0].sfx = True
        db.save_lines(did, lines)
        assert gv.find_variants(did, "ナロ")["candidates"] == []

    def test_bad_input(self, isolated_db):
        did = _title(["ナロー"])
        with pytest.raises(InvalidInputError):
            gv.find_variants(did, "   ")
        with pytest.raises(InvalidInputError):
            gv.find_variants(did, "！！！")                # no sound to compare
        with pytest.raises(InvalidInputError):
            gv.find_variants(did, "x" * 500)
        with pytest.raises(NotFoundError):
            gv.find_variants(9999, "ナロ")

    def test_title_without_series_still_searches(self, isolated_db):
        did = db.create_drama(title_en="T", source_language="ja")
        db.save_lines(did, [Line(idx=0, start=0, end=1, zh="ナロー", en="")])
        assert [c["text"] for c in gv.find_variants(did, "ナロ")["candidates"]] == ["ナロー"]


@needs_pypinyin
def test_find_variants_chinese_traditional_and_simplified(isolated_db):
    did = _title(["纳洛来了", "雅洛在这里", "納洛呢", "沈清疑说话"], language="zh")
    out = gv.find_variants(did, "纳洛")
    assert {c["text"] for c in out["candidates"]} == {"雅洛", "納洛"}
    assert all(c["score"] == 1.0 for c in out["candidates"])


def test_find_variants_korean_and_latin_need_no_library(isolated_db):
    did = _title(["나로 왔다", "나로크 어디", "지우 웃는다"], language="ko")
    assert [c["text"] for c in gv.find_variants(did, "나로크")["candidates"]] == ["나로"]
    did = _title(["Naloch said hi", "then Nalou left", "Marcus stayed"], language="ko")
    assert {c["text"] for c in gv.find_variants(did, "Nalo")["candidates"]} == {"Naloch", "Nalou"}


def test_unavailable_library_returns_a_reason(isolated_db, monkeypatch):
    did = _title(["ナロー"])
    monkeypatch.setattr(ph, "availability", lambda lang: {
        "available": False, "package": "pykakasi", "hint": "Install pykakasi."})
    out = gv.find_variants(did, "ナロ")
    assert out["available"] is False and "pykakasi" in out["reason"] and out["candidates"] == []
    assert gv.discover_names(did)["available"] is False


@needs_kakasi
class TestDiscoverNames:
    def test_groups_spellings_into_one_cluster(self, isolated_db):
        texts = ["ナローが来た", "ナロー！", "ナロウだ", "ナロックは", "ミカが笑う", "ミカも", "ミカの"]
        out = gv.discover_names(_title(texts))
        by_first = {c["suggested_original"]: c for c in out["clusters"]}
        nalo = next(c for c in out["clusters"] if "ナロー" in {s["text"] for s in c["spellings"]})
        assert {s["text"] for s in nalo["spellings"]} >= {"ナロー", "ナロウ"}
        assert nalo["total"] >= 3 and nalo["examples"]
        # ミカ is a different person and stays its own cluster.
        mika = by_first["ミカ"]
        assert [s["text"] for s in mika["spellings"]] == ["ミカ"]
        assert out["clusters"][0] is nalo                        # several spellings first

    def test_speaker_labels_and_latin_words_are_candidates(self, isolated_db):
        did = _title(["あ", "い", "Marcus is here", "Marcus left"],
                     speakers={0: "ナロ", 1: "ナロー"})
        out = gv.discover_names(did)
        spellings = {s["text"]: s for c in out["clusters"] for s in c["spellings"]}
        assert spellings["Marcus"]["sources"] == ["latin"]
        # one line each is not "recurring" on its own, but the speaker count adds up
        assert "ナロ" not in spellings or "speaker" in spellings["ナロ"]["sources"]

    def test_names_next_to_an_honorific_are_found(self, isolated_db):
        out = gv.discover_names(_title(["田中さん、おはよう", "田中さんが言った", "鈴木君は"]))
        spellings = {s["text"] for c in out["clusters"] for s in c["spellings"]}
        assert "田中" in spellings and "鈴木" not in spellings   # only one mention

    def test_covered_clusters_are_hidden_and_partial_ones_point_at_the_term(self, isolated_db):
        texts = ["ナローが", "ナローも", "ナロウは", "ナロウが"]
        did = _title(texts, terms=[("ナロー", ["ナロウ"])])
        assert gv.discover_names(did)["clusters"] == []
        did2 = _title(texts, terms=[("ナロー", [])])
        (cluster,) = gv.discover_names(did2)["clusters"]
        assert cluster["existing_term"] == "ナロー"

    def test_output_is_capped(self, isolated_db, monkeypatch):
        monkeypatch.setattr(gv, "MAX_CLUSTERS", 2)
        texts = ["アキラ", "アキラ", "ヒロシ", "ヒロシ", "ケンジ", "ケンジ", "タロウ", "タロウ"]
        assert len(gv.discover_names(_title(texts))["clusters"]) == 2

    def test_empty_title(self, isolated_db):
        out = gv.discover_names(db.create_drama(title_en="E", source_language="ja"))
        assert out["clusters"] == [] and out["lines_scanned"] == 0


class TestWorkIsBounded:
    """Korean, so these run without pykakasi/pypinyin (CI installs only the core requirements)."""

    def test_fifty_long_aliases_finish_fast(self, isolated_db):
        import time
        aliases = [("나로" * 100)[: 150 + i] for i in range(50)]
        did = _title(["나로우 왔다 " * 4] * 400, language="ko", terms=[("나로", aliases)])
        t = time.perf_counter()
        out = gv.find_variants(did, "나로")
        assert time.perf_counter() - t < 5
        assert out["available"] and {c["text"] for c in out["candidates"]} >= {"나로우"}

    def test_a_term_too_long_to_be_a_name_is_refused(self, isolated_db):
        did = _title(["나로우"], language="ko")
        with pytest.raises(InvalidInputError):
            gv.find_variants(did, "나로" * 40)

    def test_similarity_work_budget_stops_the_scan_and_says_so(self, isolated_db, monkeypatch):
        monkeypatch.setattr(gv, "MAX_SIMILARITY_CHECKS", 3)
        did = _title(["나 나로 나루 나르 나라 나리 나로우 나무"] * 10, language="ko")
        out = gv.find_variants(did, "나로")
        assert out["truncated"] is True

    def test_three_hundred_long_speaker_labels_finish_fast(self, isolated_db):
        import time
        labels = {i: ("아키라" * 40) + str(i) for i in range(300)}
        did = _title(["아"] * 300, language="ko", speakers=labels)
        t = time.perf_counter()
        out = gv.discover_names(did)
        assert time.perf_counter() - t < 5
        assert out["available"] and out["clusters"] == []        # nothing is name-sized

    def test_comparison_budget_stops_clustering_and_says_so(self, isolated_db, monkeypatch):
        monkeypatch.setattr(gv, "MAX_NAME_COMPARISONS", 3)
        names = ["아키라", "히로시", "켄지", "타로", "미카", "사쿠라", "유키"]
        did = _title(["아"] * 14, language="ko", speakers={i: names[i // 2] for i in range(14)})
        assert gv.discover_names(did)["truncated"] is True
