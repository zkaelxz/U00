"""Built-in language packs: file validation, selection, precedence, the prompt
text for ja/ko/zh, the API, and that the CLI gets the same prompt as the app."""

import argparse
import contextlib
import io
import json

import pytest

import cli
import cli_translate
import language_packs as lp
import translate_engines
from core import Line
from services import language_pack_service as svc
from services import workspace_job_service
from services.service_errors import InvalidInputError, NotFoundError


def _pack(**over):
    pack = {"id": "t-pack", "version": 1, "language": "ja", "title": "T", "description": "D",
            "entries": [{"source": "先輩", "en": "senpai"}]}
    pack.update(over)
    return pack


def _drama(db, lang, texts, series="S"):
    sid = db.get_or_create_series(series)
    did = db.create_drama(title_en="T", series_id=sid, status="aligned", source_language=lang)
    db.save_lines(did, [Line(idx=i, start=float(i), end=i + 1.0, zh=t) for i, t in enumerate(texts)])
    return did


class TestBuiltInFiles:
    def test_every_shipped_pack_is_valid_and_not_skipped(self):
        import os
        files = [n for n in os.listdir(lp.PACK_DIR) if n.endswith(".json")]
        assert sorted(p + ".json" for p in lp.all_packs()) == sorted(files)
        assert {p["language"] for p in lp.all_packs().values()} == {"ja", "ko", "zh", "any"}

    @pytest.mark.parametrize("source", ["先輩", "さん", "ちゃん", "くん", "様", "お兄ちゃん", "お姉ちゃん",
                                        "오빠", "언니", "누나", "형", "선배", "후배", "님", "씨", "사장님",
                                        "前辈", "学长", "学姐", "师兄", "师姐", "哥哥", "姐姐", "老师", "大小姐"])
    def test_requested_honorifics_are_present(self, source):
        assert any(e["source"] == source for p in lp.all_packs().values() for e in p["entries"])

    def test_default_styles_follow_the_owner_choice(self):
        packs = lp.all_packs()
        def en(pack, source, style=None):
            entry = next(e for e in packs[pack]["entries"] if e["source"] == source)
            return lp.rendering(entry, packs[pack], style)
        assert en("ja-address", "先輩") == "senpai" and en("ja-address", "先輩", "natural") == "senior"
        assert en("ko-address", "오빠") == "oppa" and en("ko-address", "언니") == "unnie"
        assert en("zh-address", "姐姐") == "big sis" and en("zh-address", "哥哥") == "big brother"
        assert en("zh-address", "姐姐", "pinyin") == "jiejie"

    def test_file_name_equals_pack_id(self):
        import os
        for name in os.listdir(lp.PACK_DIR):
            if name.endswith(".json"):
                with open(os.path.join(lp.PACK_DIR, name), encoding="utf-8") as f:
                    assert json.load(f)["id"] == name[:-len(".json")]

    def test_vtuber_packs_load_core_before_names(self):
        ids = list(lp.all_packs())
        assert ids.index("ja-vtuber-core") < ids.index("ja-vtuber-names")
        assert ids.index("zh-vtuber-core") < ids.index("zh-vtuber-names")

    @pytest.mark.parametrize("pack_id, count", [("ja-vtuber-core", 164), ("ja-vtuber-names", 42),
                                                ("zh-vtuber-core", 139), ("zh-vtuber-names", 35)])
    def test_vtuber_pack_sizes(self, pack_id, count):
        assert len(lp.all_packs()[pack_id]["entries"]) == count

    def test_vtuber_style_defaults(self):
        packs = lp.all_packs()
        assert packs["ja-vtuber-core"]["styles"]["default"] == "romanised"
        assert packs["zh-vtuber-core"]["styles"]["default"] == "fan"
        assert not packs["ja-vtuber-names"]["styles"]["options"]
        assert not packs["zh-vtuber-names"]["styles"]["options"]
        entry = next(e for e in packs["ja-vtuber-core"]["entries"] if e["source"] == "てぇてぇ")
        assert lp.rendering(entry, packs["ja-vtuber-core"]) == "teetee"
        assert lp.rendering(entry, packs["ja-vtuber-core"], "natural") == "precious"

    def test_zh_vtuber_core_has_a_traditional_twin_for_each_converted_form(self):
        sources = {e["source"] for e in lp.all_packs()["zh-vtuber-core"]["entries"]}
        twins = [("单推", "單推"), ("贴贴", "貼貼"), ("联动", "聯動"), ("舰长", "艦長"), ("总督", "總督"),
                 ("毕业", "畢業"), ("转生", "轉生"), ("个人势", "個人勢"), ("企业势", "企業勢"),
                 ("虚拟主播", "虛擬主播"), ("杂谈", "雜談"), ("同时视听", "同時視聽")]
        assert [t for t in twins if not {t[0], t[1]} <= sources] == []
        by_source = {e["source"]: e for e in lp.all_packs()["zh-vtuber-core"]["entries"]}
        assert all(by_source[s]["en"] == by_source[t]["en"] for s, t in twins)

    def test_vtuber_names_keep_the_romanised_form_in_every_style(self):
        by_source = {e["source"]: e for e in lp.all_packs()["ja-vtuber-names"]["entries"]}
        assert by_source["兎田ぺこら"]["en"] == "Usada Pekora" and by_source["こんぺこ"]["en"] == "Konpeko"

    def test_vtuber_packs_do_not_repeat_a_source_of_an_earlier_pack_of_the_language(self):
        seen = {}
        for pack in lp.all_packs().values():
            for e in pack["entries"]:
                if "vtuber" in pack["id"]:
                    assert (pack["language"], e["source"]) not in seen, (pack["id"], e["source"])
                seen.setdefault((pack["language"], e["source"]), pack["id"])

    def test_every_pack_says_it_is_a_starter_pack(self):
        assert all("Starter pack, review it" in p["description"] for p in lp.all_packs().values())


class TestValidation:
    def test_minimal_pack_is_accepted(self):
        assert lp.validate_pack(_pack(), "f.json")["entries"][0]["en"] == "senpai"

    @pytest.mark.parametrize("change, message", [
        ({"id": "Bad Id"}, "'id'"),
        ({"version": 0}, "'version'"),
        ({"language": "fr"}, "'language'"),
        ({"entries": []}, "'entries'"),
        ({"title": ""}, "'title' is required"),
        ({"entries": [{"source": "", "en": "x"}]}, "entry 1: 'source' is required"),
        ({"entries": [{"source": "a"}]}, "entry 1: 'en' is required"),
        ({"entries": [{"source": "a", "en": "b", "category": "nope"}]}, "'category'"),
        ({"entries": [{"source": "a", "en": "b"}, {"source": "a", "en": "c"}]}, "appears twice"),
        ({"entries": [{"source": "a", "en": {"x": "b"}}]}, "one rendering for each style"),
        ({"styles": {"default": "z", "options": {"x": "X"}}}, "not one of its options"),
        ({"entries": [{"source": "a", "en": "b" * 500}]}, "longer than"),
    ])
    def test_errors_name_the_problem(self, change, message):
        with pytest.raises(lp.PackError, match=message):
            lp.validate_pack(_pack(**change), "f.json")

    def test_non_object_is_refused(self):
        with pytest.raises(lp.PackError, match="one JSON object"):
            lp.validate_pack([], "f.json")

    def test_style_strings_expand_and_dicts_must_cover_every_style(self):
        styles = {"default": "a", "options": {"a": "A", "b": "B"}}
        pack = lp.validate_pack(_pack(styles=styles, entries=[{"source": "x", "en": "same"}]), "f.json")
        assert pack["entries"][0]["en"] == {"a": "same", "b": "same"}

    def test_oversized_and_broken_files_are_refused_without_a_path(self, tmp_path):
        big = tmp_path / "big.json"
        big.write_text(" " * (lp.MAX_PACK_BYTES + 1))
        bad = tmp_path / "bad.json"
        bad.write_text("{nope")
        for f, text in ((big, "larger than"), (bad, "not valid JSON")):
            with pytest.raises(lp.PackError, match=text) as err:
                lp.load_pack_file(str(f))
            assert str(tmp_path) not in str(err.value)

    def test_a_bad_file_in_the_folder_is_skipped_not_fatal(self, tmp_path, monkeypatch):
        (tmp_path / "ok.json").write_text(json.dumps(_pack()))
        (tmp_path / "bad.json").write_text(json.dumps(_pack(id="Bad")))
        monkeypatch.setattr(lp, "PACK_DIR", str(tmp_path))
        monkeypatch.setattr(lp, "_cache", None)
        try:
            assert list(lp.all_packs()) == ["t-pack"]
        finally:
            monkeypatch.setattr(lp, "_cache", None)


class TestSelection:
    def test_only_entries_found_in_the_text_are_sent(self):
        found = lp.matching_entries({"ja-address": None}, "先輩、おはよう")
        assert [e["source"] for e in found] == ["先輩"]
        assert lp.matching_entries({"ja-address": None}, "ありがとう") == []

    def test_user_term_or_alias_beats_the_pack_entry(self):
        user = [{"term_original": "先輩", "aliases": ""},
                {"term_original": "Ms. Chen", "aliases": "お姉ちゃん|x"}]
        found = lp.matching_entries({"ja-address": None}, "先輩 お姉ちゃん お兄ちゃん", user)
        sources = [e["source"] for e in found]
        assert "お兄ちゃん" in sources and "先輩" not in sources and "お姉ちゃん" not in sources

    def test_unknown_pack_ids_are_ignored_and_order_is_stable(self):
        text = "姐姐 哥哥 前辈"
        a = lp.matching_entries({"gone": None, "zh-address": None}, text)
        assert a == lp.matching_entries({"zh-address": None}, text)
        assert [e["source"] for e in a] == ["前辈", "哥哥", "姐姐"]

    def test_order_follows_pack_order_not_the_choices_dict(self):
        a = lp.matching_entries({"zh-address": None, "shared-drama": None}, "老师 CP")
        b = lp.matching_entries({"shared-drama": None, "zh-address": None}, "老师 CP")
        assert a == b and [e["pack"] for e in a] == list(dict.fromkeys(e["pack"] for e in a))
        order = list(lp.all_packs())
        assert [e["pack"] for e in a] == sorted((e["pack"] for e in a), key=order.index)

    @pytest.mark.parametrize("pack, text, absent", [
        ("ja-address", "お疲れ様でした", "様"),
        ("ja-address", "様子がおかしい、同様です、様々な人", "様"),
        ("ko-address", "날씨가 좋아요, 글씨, 씨앗", "씨"),
        ("ko-address", "아저씨, 아가씨", "씨"),
        ("ko-address", "인형이랑 형사, 형식, 형편", "형"),
        ("shared-drama", "TOP COPY EDIT", "OP"),
    ])
    def test_short_entries_do_not_fire_inside_other_words(self, pack, text, absent):
        language = lp.all_packs()[pack]["language"]
        found = lp.matching_entries({pack: None}, text, language=None if language == "any" else language)
        assert absent not in [e["source"] for e in found]

    def test_users_longer_term_blocks_the_short_entry(self):
        terms = [{"term_original": "お嬢様", "aliases": ""}]
        assert lp.matching_entries({"ja-address": None}, "お嬢様", terms) == []
        assert "様" in [e["source"] for e in lp.matching_entries({"ja-address": None}, "田中様")]

    def test_longer_entry_consumes_its_span_but_a_separate_use_still_matches(self):
        sources = [e["source"] for e in lp.matching_entries({"ja-address": None, "ja-common": None}, "お疲れ様 田中様")]
        assert "お疲れ様" in sources and "様" in sources
        assert "様" not in [e["source"] for e in lp.matching_entries({"ja-address": None, "ja-common": None}, "お疲れ様")]

    def test_ascii_entries_need_word_boundaries(self):
        assert [e["source"] for e in lp.matching_entries({"shared-drama": None}, "the OP and ED")] == ["OP", "ED"]
        assert lp.matching_entries({"shared-drama": None}, "TOPS CPU") == []

    def test_traditional_script_text_matches(self):
        found = lp.matching_entries({"zh-address": None, "zh-common": None}, "老師，學長說媽媽的彈幕")
        assert {"老師", "學長", "媽媽", "彈幕"} <= {e["source"] for e in found}

    def test_every_simplified_zh_entry_has_a_traditional_twin(self):
        # Only the characters the shipped entries use; a dependency for full conversion isn't worth it.
        to_traditional = str.maketrans({
            "师": "師", "辈": "輩", "长": "長", "妈": "媽", "弹": "彈",
            "闺": "閨", "总": "總", "马": "馬", "赏": "賞", "学": "學",
        })
        for pack_id in ("zh-address", "zh-common"):
            sources = {e["source"] for e in lp.all_packs()[pack_id]["entries"]}
            missing = [s for s in sources if s.translate(to_traditional) != s
                       and s.translate(to_traditional) not in sources]
            assert not missing, f"{pack_id} lacks a traditional form for {missing}"

    def test_traditional_cultivation_and_streamer_terms_match(self):
        found = lp.matching_entries({"zh-address": None, "zh-common": None}, "師兄、師姐、師弟、師妹、師尊、師父，青梅竹馬，打賞")
        assert {"師兄", "師姐", "師弟", "師妹", "師尊", "師父", "青梅竹馬", "打賞"} <= {e["source"] for e in found}

    def test_longer_word_of_a_turned_off_pack_still_blocks_a_short_entry(self):
        ko = lp.matching_entries({"ko-address": None}, "회장님")
        assert "님" not in [e["source"] for e in ko]
        ja = lp.matching_entries({"ja-address": None}, "お疲れ様")
        assert "様" not in [e["source"] for e in ja]

    def test_matching_is_linear_in_the_text_length(self):
        import time
        text = "田中さん、山田様。" * 25000
        start = time.perf_counter()
        found = lp.matching_entries({"ja-address": None}, text)
        assert time.perf_counter() - start < 10.0
        assert {"さん", "様"} <= {e["source"] for e in found}

    def test_more_than_the_cap_keeps_the_most_frequent_entries(self):
        lp._cache = {"t-pack": lp.validate_pack(
            _pack(entries=[{"source": f"w{i:03d}", "en": "x"} for i in range(100)]), "f")}
        try:
            # the last 10 entries in file order appear 3 times, all others once
            text = " ".join(f"w{i:03d}" for i in range(100)) + " " + " ".join(
                f"w{i:03d}" for i in list(range(90, 100)) * 2)
            found, dropped = lp.select_entries({"t-pack": None}, text)
            sources = [e["source"] for e in found]
            assert len(sources) == lp.MAX_PROMPT_ENTRIES and dropped == 100 - lp.MAX_PROMPT_ENTRIES
            assert sources[:10] == [f"w{i:03d}" for i in range(90, 100)]
            assert sources[10:13] == ["w000", "w001", "w002"]
        finally:
            lp._cache = None

    def test_equal_counts_keep_pack_then_file_order(self):
        lp._cache = {"a-pack": lp.validate_pack(_pack(id="a-pack", entries=[
                         {"source": "bb", "en": "1"}, {"source": "aa", "en": "2"}]), "f"),
                     "b-pack": lp.validate_pack(_pack(id="b-pack", entries=[{"source": "cc", "en": "3"}]), "f")}
        try:
            found = lp.matching_entries({"b-pack": None, "a-pack": None}, "cc aa bb aa bb cc")
            assert [e["source"] for e in found] == ["bb", "aa", "cc"]
            found = lp.matching_entries({"a-pack": None, "b-pack": None}, "cc cc aa bb")
            assert [e["source"] for e in found] == ["cc", "bb", "aa"]
        finally:
            lp._cache = None

    def test_only_spans_not_claimed_by_a_longer_word_count(self):
        lp._cache = {"t-pack": lp.validate_pack(_pack(entries=[
            {"source": "短", "en": "s"}, {"source": "短い", "en": "l"}, {"source": "他", "en": "o"}]), "f")}
        try:
            # 短 occurs 3 times but two of them sit inside 短い, so it counts once
            found = lp.matching_entries({"t-pack": None}, "短い 短い 短 他 他", limit=2)
            assert [e["source"] for e in found] == ["短い", "他"]
        finally:
            lp._cache = None

    def test_matching_entries_is_the_entries_of_select_entries(self):
        text = "田中さん、山田様。先輩"
        assert lp.matching_entries({"ja-address": None}, text) == lp.select_entries({"ja-address": None}, text)[0]
        assert lp.select_entries({"ja-address": None}, "ありがとう") == ([], 0)

    def test_prompt_is_capped(self):
        pack = lp.validate_pack(_pack(entries=[{"source": f"w{i}", "en": "x"} for i in range(100)]), "f")
        lp._cache = {"t-pack": pack}
        try:
            text = " ".join(f"w{i}" for i in range(100))
            assert len(lp.matching_entries({"t-pack": None}, text)) == lp.MAX_PROMPT_ENTRIES
        finally:
            lp._cache = None

    @pytest.mark.parametrize("pack, text, expected", [
        ("ja-address", "先輩、行きましょう", "先輩 → default: senpai"),
        ("ko-address", "오빠, 같이 가요", "오빠 → default: oppa"),
        ("zh-address", "姐姐，我们走吧", "姐姐 → default: big sis"),
    ])
    def test_prompt_text(self, pack, text, expected):
        block = lp.build_pack_block(lp.matching_entries({pack: None}, text))
        assert expected in block and "a glossary term with the same source always wins" in block

    def test_context_and_note_are_marked_and_the_default_is_a_default(self):
        block = lp.build_pack_block(lp.matching_entries({"ko-address": "natural"}, "아저씨"))
        assert "아저씨 → default: mister (use when: to a middle-aged man; note: " in block
        assert "Pack entries are defaults" in block

    def test_entry_without_context_or_note_has_no_parentheses(self):
        block = lp.build_pack_block([{"source": "x", "en": "y", "context": "", "note": ""}])
        assert block.endswith("  x → default: y")

    def test_block_is_identical_for_the_same_run_text(self):
        text = "老婆 阿姨 叔叔 大小姐 姐姐"
        first = lp.build_pack_block(lp.matching_entries({"zh-address": None, "zh-common": None}, text))
        assert first == lp.build_pack_block(lp.matching_entries({"zh-address": None, "zh-common": None}, text))

    def test_capped_prompt_is_identical_across_calls(self):
        pack = lp.validate_pack(_pack(entries=[{"source": f"w{i:03d}", "en": "x"} for i in range(100)]), "f")
        lp._cache = {"t-pack": pack}
        try:
            text = " ".join(f"w{i:03d}" for i in range(100)) + " w050 w099 w099"
            blocks = {lp.build_pack_block(lp.matching_entries({"t-pack": None}, text)) for _ in range(3)}
            assert len(blocks) == 1 and "w099 → default" in blocks.pop().split("\n")[1]
        finally:
            lp._cache = None

    def test_full_block_stays_bounded(self):
        # 60 entries is the cap; the zh set fills it. About 5.2k chars (~1.5k tokens) today.
        packs = [p for p in lp.all_packs().values() if p["language"] in ("zh", "any")]
        text = " ".join(e["source"] for p in packs for e in p["entries"])
        entries = lp.matching_entries({p["id"]: None for p in packs}, text, language="zh")
        assert len(entries) == lp.MAX_PROMPT_ENTRIES
        assert len(lp.build_pack_block(entries)) <= 6500

    @pytest.mark.parametrize("pack, source, must_mention", [
        ("zh-address", "老婆", ["my wife", "babe"]),
        ("zh-common", "阿姨", ["ma'am", "housekeeper"]),
        ("zh-common", "叔叔", ["sir", "mister"]),
        ("zh-address", "大小姐", ["Milady", "Young Miss"]),
        ("ja-common", "社長", ["president", "CEO"]),
        ("ko-address", "아저씨", ["ajusshi", "old man", "buddy"]),
    ])
    def test_contested_entries_name_their_alternatives(self, pack, source, must_mention):
        entry = next(e for e in lp.all_packs()[pack]["entries"] if e["source"] == source)
        assert all(w.lower() in entry["note"].lower() for w in must_mention)

    def test_no_entries_means_no_block(self):
        assert lp.build_pack_block([]) == ""


class TestTitleChoice:
    def test_off_by_default_and_not_in_the_prompt(self, isolated_db):
        did = _drama(isolated_db, "ja", ["先輩"])
        assert not any(p["enabled"] for p in svc.get_title_packs(did)["packs"])
        _, guidelines, _ = workspace_job_service.build_run_style_context(
            did, isolated_db.get_drama(did), cli.lines_from_rows(isolated_db.load_lines(did)), "audio_drama")
        assert "LANGUAGE PACK" not in guidelines

    def test_enabled_pack_reaches_the_prompt_and_user_term_wins(self, isolated_db):
        did = _drama(isolated_db, "ja", ["先輩", "お兄ちゃん"])
        svc.set_title_packs(did, {"packs": {"ja-address": None}})
        d, lines = isolated_db.get_drama(did), cli.lines_from_rows(isolated_db.load_lines(did))
        _, g, _ = workspace_job_service.build_run_style_context(did, d, lines, "audio_drama")
        assert "先輩 → default: senpai" in g and "お兄ちゃん → default: onii-chan" in g
        isolated_db.upsert_glossary_term(d["series_id"], "先輩", "Senior Li")
        glossary, g, _ = workspace_job_service.build_run_style_context(did, d, lines, "audio_drama")
        assert "先輩 → default: senpai" not in g and "先輩 → Senior Li" in g
        assert [t["term_original"] for t in glossary] == ["先輩"]

    def test_style_flip_changes_the_wording(self, isolated_db):
        did = _drama(isolated_db, "zh", ["姐姐"])
        svc.set_title_packs(did, {"packs": {"zh-address": "pinyin"}})
        assert "姐姐 → default: jiejie" in svc.block_for(isolated_db.get_drama(did), [Line(0, 0, 1, "姐姐")])

    def test_language_default_applies_until_the_title_chooses(self, isolated_db):
        did = _drama(isolated_db, "ko", ["오빠"])
        svc.set_language_default("ko", {"packs": {"ko-address": None}})
        assert svc.get_title_packs(did)["uses_default"] is True
        assert "오빠 → default: oppa" in svc.block_for(isolated_db.get_drama(did), [Line(0, 0, 1, "오빠")])
        svc.set_title_packs(did, {"packs": {}})
        assert svc.get_title_packs(did)["uses_default"] is False
        assert svc.block_for(isolated_db.get_drama(did), [Line(0, 0, 1, "오빠")]) == ""

    def test_only_packs_for_the_titles_language_are_offered_and_accepted(self, isolated_db):
        did = _drama(isolated_db, "ja", ["先輩"])
        ids = {p["id"] for p in svc.get_title_packs(did)["packs"]}
        assert ids == {"ja-address", "ja-common", "ja-vtuber-core", "ja-vtuber-names", "shared-drama"}
        with pytest.raises(InvalidInputError):
            svc.set_title_packs(did, {"packs": {"ko-address": None}})
        with pytest.raises(InvalidInputError):
            svc.set_title_packs(did, {"packs": {"ja-address": "bogus"}})

    def test_entries_for_text_serves_other_callers(self, isolated_db):
        did = _drama(isolated_db, "zh", ["x"])
        svc.set_title_packs(did, {"packs": {"zh-address": None}})
        found = svc.entries_for_text(isolated_db.get_drama(did), "老师好")
        assert [e["source"] for e in found] == ["老师"]

    def test_packs_of_a_previous_source_language_stop_applying(self, isolated_db):
        did = _drama(isolated_db, "ja", ["先生"])
        svc.set_title_packs(did, {"packs": {"ja-address": None}})
        assert svc.entries_for_text(isolated_db.get_drama(did), "先生")
        isolated_db.update_drama(did, source_language="ko")
        assert svc.entries_for_text(isolated_db.get_drama(did), "先生") == []
        assert svc.block_for(isolated_db.get_drama(did), [Line(0, 0, 1, "先生")]) == ""

    def test_dropped_matches_are_logged_without_the_text(self, isolated_db, monkeypatch):
        import applog
        pack = lp.validate_pack(_pack(entries=[{"source": f"w{i:03d}", "en": "x"} for i in range(70)]), "f")
        monkeypatch.setattr(lp, "_cache", {"t-pack": pack})
        did = _drama(isolated_db, "ja", ["x"])
        svc.set_title_packs(did, {"packs": {"t-pack": None}})
        text = " ".join(f"w{i:03d}" for i in range(70))
        block = svc.block_for(isolated_db.get_drama(did), [Line(0, 0, 1, text)])
        log = "\n".join(applog.tail(20))
        assert block.count("→ default") == lp.MAX_PROMPT_ENTRIES
        assert "10 matching entries" in log and f"title {did}" in log and "w000" not in log
        svc.block_for(isolated_db.get_drama(did), [Line(0, 0, 1, "w001")])
        assert "\n".join(applog.tail(20)).count("matching entries") == 1

    def test_block_for_is_identical_for_the_same_run(self, isolated_db):
        did = _drama(isolated_db, "zh", ["x"])
        svc.set_title_packs(did, {"packs": {"zh-address": None, "zh-common": None, "zh-vtuber-core": None}})
        lines = [Line(0, 0, 1, "姐姐 老师 联动 联动 草"), Line(1, 1, 2, "哥哥 联动 毕业")]
        drama = isolated_db.get_drama(did)
        assert svc.block_for(drama, lines) == svc.block_for(drama, list(lines)) != ""

    def test_unknown_pack_and_title(self, isolated_db):
        with pytest.raises(NotFoundError):
            svc.get_pack("nope")
        with pytest.raises(NotFoundError):
            svc.get_title_packs(9999)

    def test_cli_translate_sends_the_same_prompt_as_the_app(self, isolated_db, monkeypatch):
        did = _drama(isolated_db, "ko", ["오빠, 가자"])
        svc.set_title_packs(did, {"packs": {"ko-address": None}})
        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: object())
        seen = {}
        monkeypatch.setattr(translate_engines, "translate_lines_with_engine",
                            lambda lines, engine, **kw: (seen.update(kw), (lines, []))[1])
        args = argparse.Namespace(id=did, status=None, engine="claude", api_key="k", model=None,
                                  style_note=None, style_preset="audio_drama", locale="en-US",
                                  force=False, ollama_num_ctx=None)
        with contextlib.redirect_stdout(io.StringIO()):
            cli_translate.cmd_translate(args)
        lines = cli.lines_from_rows(isolated_db.load_lines(did))
        _, expected, _ = workspace_job_service.build_run_style_context(
            did, isolated_db.get_drama(did), lines, "audio_drama")
        assert seen["style_guidelines"] == expected and "오빠 → default: oppa" in expected
