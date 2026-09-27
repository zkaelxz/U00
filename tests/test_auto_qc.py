"""Step 12b: Auto QC's factual-detail check (auto_qc.py) and its
Workspace wiring (tabs/workspace_tab._run_auto_qc)."""

import pytest

import auto_qc
import db
import translate_engines
from core import Line, adopt_ids

NAMES = auto_qc.build_name_list(
    [{"term_original": "林晚晚", "term_translation": "Lin Wanwan", "category": "person_name"},
     {"term_original": "青云宗", "term_translation": "Azure Cloud Sect", "category": "clan_sect"},
     {"term_original": "师兄", "term_translation": "Senior Brother", "category": "honorific"}],
    [{"character_name": "Xiao Bai", "aliases": "小白|Bai"}])


def _kinds(issues, direction):
    return sorted(i["kind"] for i in issues if i["direction"] == direction)


# ---------------------------------------------------------------- missing

@pytest.mark.parametrize("src, tgt, kind, text", [
    ("他欠我三百块钱。", "He owes me money.", "amount", "三百"),
    ("我们三月五号见。", "See you in March.", "date", "五"),
    ("我等了十年。", "I waited for years.", "date", "十"),
    ("明天九点见。", "See you tomorrow morning.", "time", "九"),
    ("还有五公里。", "It's still a long way.", "unit", "五"),
    ("他有三个孩子。", "He has kids.", "number", "三"),
    ("房租是2500元。", "The rent is steep.", "amount", "2500"),
    ("林晚晚来了。", "She's here.", "name", "林晚晚"),
    ("小白呢？", "Where is he?", "name", "小白"),
])
def test_missing_detail_is_flagged_with_what_is_missing(src, tgt, kind, text):
    issues = auto_qc.check_line(src, tgt, NAMES)
    assert {"direction": "missing", "kind": kind, "text": text} in issues
    note = auto_qc.issue_note(issues)
    assert text in note and "In the source but not the translation" in note


# ------------------------------------------------------------------ extra

@pytest.mark.parametrize("src, tgt, text", [
    ("我等了十年。", "I waited 10 years and 5 months.", "5"),
    ("他来了。", "He arrived at 3 o'clock.", "3"),
    ("我们走吧。", "Let's go, the two of us.", "two"),
    ("我们走吧。", "Let's go, all 1,200 of us.", "1,200"),
])
def test_number_not_in_source_is_flagged_as_likely_hallucination(src, tgt, text):
    issues = auto_qc.check_line(src, tgt, NAMES)
    assert {"direction": "extra", "kind": "number", "text": text} in issues
    assert text in auto_qc.issue_note(issues)
    assert "In the translation but not the source" in auto_qc.issue_note(issues)


# ------------------------------------------------------------------ clean

@pytest.mark.parametrize("src, tgt", [
    ("他欠我三百块钱。", "He owes me 300 yuan."),
    ("他欠我三百块钱。", "He owes me three hundred bucks."),
    ("他欠我一百块钱。", "He owes me about $14."),            # converted, not dropped
    ("还有五公里。", "Three more miles."),                    # converted unit
    ("我们三月五号见。", "See you on March 5th."),
    ("我们三月五号见。", "See you on the fifth of March."),
    ("星期三见。", "See you Wednesday."),
    ("三点半见。", "See you at 3:30."),
    ("我二〇二四年毕业的。", "I graduated in 2024."),
    ("三万五千人来了。", "Thirty-five thousand people came."),
    ("他一百零五岁了。", "He's one hundred and five."),
    ("百分之五十的机会。", "A 50% chance."),
    ("来了两次。", "Came twice."),
    ("第三楼。", "The third floor."),
    ("一石二鸟。", "Kill two birds with one stone."),
    ("林晚晚来了。", "Wanwan is here."),                       # part of a full name is enough
    ("林晚晚加入了青云宗。", "Lin Wanwan joined the Azure Cloud Sect."),
    ("小白呢？", "Where's Bai?"),
    ("师兄好。", "Hello!"),                                    # honorifics aren't checked
    # Bare CJK numerals that are words, not quantities:
    ("十分感谢。", "Thank you very much."),
    ("一样的。", "The same."),
    ("千万别去！", "Whatever you do, don't go!"),
    ("万一他来了呢？", "What if he comes?"),
    ("四周都是人。", "There are people all around."),
    ("一个人也没有。", "No one at all."),
])
def test_correctly_carried_over_details_are_not_flagged(src, tgt):
    assert auto_qc.check_line(src, tgt, NAMES) == []


def test_blank_source_or_translation_is_not_checked():
    assert auto_qc.check_line("三百块", "", NAMES) == []
    assert auto_qc.check_line("", "300 dollars", NAMES) == []


def test_word_numbers_in_translation_ignored_for_a_non_cjk_source():
    # A Spanish "dos" can't be read, so "two" mustn't count as invented;
    # digits are still compared.
    assert auto_qc.check_line("Somos dos.", "There are two of us.") == []
    assert _kinds(auto_qc.check_line("Somos dos.", "There are 7 of us."), "extra") == ["number"]


def test_cjk_numeral_parsing():
    assert auto_qc.parse_cjk_numeral("一百零五") == 105
    assert auto_qc.parse_cjk_numeral("三万五千") == 35000
    assert auto_qc.parse_cjk_numeral("十五") == 15
    assert auto_qc.parse_cjk_numeral("两百万") == 2_000_000
    assert auto_qc.parse_cjk_numeral("二〇二四") == 2024
    assert auto_qc.parse_cjk_numeral("好") is None


def test_name_list_only_uses_name_categories_and_cjk_aliases():
    names = auto_qc.build_name_list(
        [{"term_original": "师兄", "term_translation": "Senior Brother", "category": "honorific"},
         {"term_original": "林", "term_translation": "Lin", "category": "person_name"},  # 1 char
         {"term_original": "青云宗", "term_translation": "", "category": "clan_sect"}],
        [{"character_name": "Ann", "aliases": "Annie"}])          # no source-language alias
    assert names == []


def test_name_list_builds_multiple_source_and_target_forms_from_glossary_aliases():
    """Step 30: closes the asymmetry with the series_characters branch --
    a glossary term with recorded aliases now gets multiple source forms
    (other CJK spellings) and multiple target forms (romanized alias
    forms alongside the canonical translation), the same richness
    series_characters.aliases already produced below."""
    names = auto_qc.build_name_list(
        [{"term_original": "沈清疑", "term_translation": "Shen Qingyi", "category": "person_name",
          "aliases": "沈清儀|Shen Qing Yi"}])
    assert len(names) == 1
    src_forms, tgt_forms = names[0]
    assert set(src_forms) == {"沈清疑", "沈清儀"}
    assert set(tgt_forms) == {"Shen Qingyi", "Shen Qing Yi"}

    # And it's put to use: a line using only the CJK alias, translated with
    # only the romanized alias, still counts as the name being carried over.
    issues = auto_qc.check_line("沈清儀来了。", "Shen Qing Yi is here.", names)
    assert issues == []


def test_build_banned_terms_uses_term_original_and_aliases_as_source_forms():
    banned = auto_qc.build_banned_terms(
        [{"term_original": "沈清疑", "aliases": "沈清儀", "banned_translations": "Chen Qingyi|Shen Qingyu"},
         {"term_original": "无用词", "banned_translations": ""},   # no banned list -- excluded
         {"term_original": "另一个词", "term_translation": "Whatever"}])  # no field at all -- excluded
    assert banned == [(("沈清疑", "沈清儀"), ("Chen Qingyi", "Shen Qingyu"))]


def test_check_line_flags_banned_translation_but_leaves_canonical_untouched():
    """Step 30: banned_translations is flag-only -- it never rewrites the
    line, unlike the older enforce_exact hard-substitution mechanism."""
    banned = auto_qc.build_banned_terms(
        [{"term_original": "沈清疑", "banned_translations": "Chen Qingyi|Shen Qingyu"}])

    bad_issues = auto_qc.check_line("沈清疑来了。", "Chen Qingyi is here.", banned_terms=banned)
    assert {"direction": "banned", "kind": "banned_translation", "text": "Chen Qingyi"} in bad_issues
    note = auto_qc.issue_note(bad_issues)
    assert "Chen Qingyi" in note and "flagged as prohibited" in note

    good_issues = auto_qc.check_line("沈清疑来了。", "Shen Qingyi is here.", banned_terms=banned)
    assert good_issues == []


def test_run_auto_qc_flags_banned_translation_without_rewriting_the_line():
    banned = auto_qc.build_banned_terms(
        [{"term_original": "沈清疑", "banned_translations": "Chen Qingyi"}])
    bad = _ln(0, "沈清疑来了。", "Chen Qingyi is here.")
    good = _ln(1, "沈清疑来了。", "Shen Qingyi is here.")
    result = auto_qc.run_auto_qc([bad, good], banned_terms=banned)
    assert result["flagged"] == 1
    assert bad.flag == auto_qc.AUTO_QC_FLAG
    assert "Chen Qingyi" in bad.flag_note
    assert bad.en == "Chen Qingyi is here."     # flagged, not rewritten
    assert good.flag is None and good.en == "Shen Qingyi is here."


# ------------------------------------------------------- flags on lines

def _ln(idx, zh, en, **kw):
    return Line(idx=idx, start=idx * 2.0, end=idx * 2.0 + 1.5, zh=zh, en=en, **kw)


def test_run_auto_qc_flags_via_the_normal_flag_fields():
    bad = _ln(0, "他欠我三百块钱。", "He owes me money.")
    good = _ln(1, "他欠我三百块钱。", "He owes me 300 yuan.")
    result = auto_qc.run_auto_qc([bad, good], NAMES)
    assert result == {"flagged": 1, "cleared": 0, "already_flagged": 0, "checked": 2}
    assert bad.flag == auto_qc.AUTO_QC_FLAG and "三百" in bad.flag_note
    assert good.flag is None
    # The review table shows a label for it, like every other flag source.
    assert translate_engines.flag_reason_label(bad.flag).startswith("Auto QC")


def test_run_auto_qc_never_replaces_another_checks_flag():
    ln = _ln(0, "他欠我三百块钱。", "He owes me money.", flag="uncertain_translation",
             flag_note="ambiguous 'her'")
    result = auto_qc.run_auto_qc([ln], NAMES)
    assert result["already_flagged"] == 1 and result["flagged"] == 0
    assert (ln.flag, ln.flag_note) == ("uncertain_translation", "ambiguous 'her'")


def test_rerun_clears_its_own_flag_once_fixed():
    ln = _ln(0, "他欠我三百块钱。", "He owes me money.")
    auto_qc.run_auto_qc([ln], NAMES)
    ln.en = "He owes me 300 yuan."
    result = auto_qc.run_auto_qc([ln], NAMES)
    assert result["cleared"] == 1 and ln.flag is None and ln.flag_note == ""


def test_run_auto_qc_workspace_helper_saves_flags_and_uses_series_names(isolated_db):
    import tabs.workspace_tab as wt
    sid = isolated_db.get_or_create_series("S")
    isolated_db.upsert_glossary_term(sid, "林晚晚", "Lin Wanwan", category="person_name")
    did = isolated_db.create_drama(title_en="D", series_id=sid)
    isolated_db.save_lines(did, [_ln(0, "林晚晚来了。", "She's here."),
                                 _ln(1, "他欠我三百块钱。", "He owes me 300 yuan."),
                                 _ln(2, "我等了十年。", "I waited 10 years and 5 months.")])
    lines = isolated_db.load_line_objects(did)
    result = wt._run_auto_qc(did, isolated_db.get_drama(did), lines)
    assert result["flagged"] == 2

    rows = isolated_db.load_lines(did)
    assert [r["flag"] for r in rows] == [auto_qc.AUTO_QC_FLAG, None, auto_qc.AUTO_QC_FLAG]
    assert "林晚晚 (name)" in rows[0]["flag_note"]
    assert "5" in rows[2]["flag_note"]
    # Only flag fields are written -- the text is untouched.
    assert [r["en"] for r in rows] == ["She's here.", "He owes me 300 yuan.",
                                       "I waited 10 years and 5 months."]


def test_flagged_line_restores_from_version_history_like_any_other(isolated_db):
    """A good translation is snapshotted, then replaced by a bad one Auto QC
    flags; restoring the snapshot brings the good text back through the
    same adopt_ids path the Version history button uses."""
    did = isolated_db.create_drama(title_en="D")
    isolated_db.save_lines(did, [_ln(0, "他欠我三百块钱。", "He owes me 300 yuan.")])
    lines = isolated_db.load_line_objects(did)
    isolated_db.save_line_history_snapshot(did, lines, "before re-translate")
    lines[0].en = "He owes me money."
    isolated_db.save_lines(did, lines)

    import tabs.workspace_tab as wt
    lines = isolated_db.load_line_objects(did)
    wt._run_auto_qc(did, isolated_db.get_drama(did), lines)
    assert isolated_db.load_lines(did)[0]["flag"] == auto_qc.AUTO_QC_FLAG

    snap_id = isolated_db.list_line_history(did)[0]["id"]
    restored = adopt_ids([Line(**s) for s in isolated_db.get_line_history_snapshot(snap_id)],
                         isolated_db.load_line_objects(did))
    isolated_db.save_lines(did, restored)
    row = isolated_db.load_lines(did)[0]
    assert row["en"] == "He owes me 300 yuan." and row["id"] == lines[0].id

    # Re-running Auto QC on the restored line clears the now-stale flag.
    restored = isolated_db.load_line_objects(did)
    assert wt._run_auto_qc(did, isolated_db.get_drama(did), restored)["cleared"] == 1
    assert isolated_db.load_lines(did)[0]["flag"] is None


def test_run_auto_qc_button_flags_lines_in_the_review_queue(isolated_db):
    """The button in Review queue writes the flag and reports the count;
    merely opening the page writes nothing."""
    from streamlit.testing.v1 import AppTest
    did = isolated_db.create_drama(title_en="D", media_type="audio_drama",
                                   content_mode="audio_drama", status="translated")
    isolated_db.save_lines(did, [_ln(0, "他欠我三百块钱。", "He owes me money."),
                                 _ln(1, "我等了十年。", "I waited ten years.")])

    def _render():
        import tabs.workspace_tab as wt
        wt.render_workspace_tab()
    at = AppTest.from_function(_render)
    at.session_state["active_drama_id"] = did
    at.session_state["lines"] = None
    at.run(timeout=30)
    assert isolated_db.load_lines(did)[0]["flag"] is None

    [b for b in at.button if b.key == f"run_auto_qc_{did}"][0].click()
    at.run(timeout=30)

    rows = isolated_db.load_lines(did)
    assert [r["flag"] for r in rows] == [auto_qc.AUTO_QC_FLAG, None]
    assert any("Auto QC flagged 1 of 2" in w.value for w in at.warning)
    # Shown in the review table like any other flag.
    assert any("Auto QC" in w.value and "三百" in w.value for w in at.warning)


def test_auto_qc_before_export_is_read_only_until_clicked(isolated_db):
    """Step 12e's "Auto QC before export" toggle (on in the Release tier)
    lists mismatches in the Export section; rendering writes nothing, the
    button flags them."""
    from streamlit.testing.v1 import AppTest
    did = isolated_db.create_drama(title_en="D", media_type="audio_drama",
                                   content_mode="audio_drama", status="translated")
    isolated_db.save_lines(did, [_ln(0, "他欠我三百块钱。", "He owes me money."),
                                 _ln(1, "我等了十年。", "I waited ten years.")])

    def _render():
        import tabs.workspace_tab as wt
        wt.render_workspace_tab()
    at = AppTest.from_function(_render)
    at.session_state["active_drama_id"] = did
    at.session_state["lines"] = None
    at.run(timeout=30)
    assert not any("Auto QC: " in w.value and "compared with the source" in w.value for w in at.warning)  # off by default

    at.checkbox(key=f"auto_qc_{did}").check().run(timeout=30)
    assert not at.exception
    assert any("Auto QC: 1 line(s)" in w.value and "#1" in w.value for w in at.warning)
    assert isolated_db.load_lines(did)[0]["flag"] is None  # not written just from rendering

    at.button(key=f"flag_auto_qc_{did}").click().run(timeout=30)
    assert [r["flag"] for r in isolated_db.load_lines(did)] == [auto_qc.AUTO_QC_FLAG, None]
