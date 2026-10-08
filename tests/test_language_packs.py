"""Built-in language packs: file validation, selection, precedence, the prompt
text for ja/ko/zh, the API, and that the CLI gets the same prompt as the app."""

import argparse
import contextlib
import io
import json

import pytest

import cli
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

    def test_prompt_is_capped(self):
        pack = lp.validate_pack(_pack(entries=[{"source": f"w{i}", "en": "x"} for i in range(100)]), "f")
        lp._cache = {"t-pack": pack}
        try:
            text = " ".join(f"w{i}" for i in range(100))
            assert len(lp.matching_entries({"t-pack": None}, text)) == lp.MAX_PROMPT_ENTRIES
        finally:
            lp._cache = None

    @pytest.mark.parametrize("pack, text, expected", [
        ("ja-address", "先輩、行きましょう", "先輩 → senpai"),
        ("ko-address", "오빠, 같이 가요", "오빠 → oppa"),
        ("zh-address", "姐姐，我们走吧", "姐姐 → big sis"),
    ])
    def test_prompt_text(self, pack, text, expected):
        block = lp.build_pack_block(lp.matching_entries({pack: None}, text))
        assert expected in block and "A term in the glossary above always wins" in block

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
        assert "先輩 → senpai" in g and "お兄ちゃん → onii-chan" in g
        isolated_db.upsert_glossary_term(d["series_id"], "先輩", "Senior Li")
        glossary, g, _ = workspace_job_service.build_run_style_context(did, d, lines, "audio_drama")
        assert "先輩 → senpai" not in g and "先輩 → Senior Li" in g
        assert [t["term_original"] for t in glossary] == ["先輩"]

    def test_style_flip_changes_the_wording(self, isolated_db):
        did = _drama(isolated_db, "zh", ["姐姐"])
        svc.set_title_packs(did, {"packs": {"zh-address": "pinyin"}})
        assert "姐姐 → jiejie" in svc.block_for(isolated_db.get_drama(did), [Line(0, 0, 1, "姐姐")])

    def test_language_default_applies_until_the_title_chooses(self, isolated_db):
        did = _drama(isolated_db, "ko", ["오빠"])
        svc.set_language_default("ko", {"packs": {"ko-address": None}})
        assert svc.get_title_packs(did)["uses_default"] is True
        assert "오빠 → oppa" in svc.block_for(isolated_db.get_drama(did), [Line(0, 0, 1, "오빠")])
        svc.set_title_packs(did, {"packs": {}})
        assert svc.get_title_packs(did)["uses_default"] is False
        assert svc.block_for(isolated_db.get_drama(did), [Line(0, 0, 1, "오빠")]) == ""

    def test_only_packs_for_the_titles_language_are_offered_and_accepted(self, isolated_db):
        did = _drama(isolated_db, "ja", ["先輩"])
        ids = {p["id"] for p in svc.get_title_packs(did)["packs"]}
        assert ids == {"ja-address", "ja-common", "shared-drama"}
        with pytest.raises(InvalidInputError):
            svc.set_title_packs(did, {"packs": {"ko-address": None}})
        with pytest.raises(InvalidInputError):
            svc.set_title_packs(did, {"packs": {"ja-address": "bogus"}})

    def test_entries_for_text_serves_other_callers(self, isolated_db):
        did = _drama(isolated_db, "zh", ["x"])
        svc.set_title_packs(did, {"packs": {"zh-address": None}})
        found = svc.entries_for_text(isolated_db.get_drama(did), "老师好")
        assert [e["source"] for e in found] == ["老师"]

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
            cli.cmd_translate(args)
        lines = cli.lines_from_rows(isolated_db.load_lines(did))
        _, expected, _ = workspace_job_service.build_run_style_context(
            did, isolated_db.get_drama(did), lines, "audio_drama")
        assert seen["style_guidelines"] == expected and "오빠 → oppa" in expected
