"""tools/vtuber_name_packs.py: profile parsing, the held-back rules, and that hand-written
entries and overrides survive a rebuild. No network: pages are inline fixtures."""

import json

import pytest

from tools import vtuber_name_packs as vnp

PROFILE = """
<h1 class="fs-4 m-0">Sample Rin
</h1>
<section id="original-name" class="mb-4"><h2>Original Name</h2><p>
範例·鈴</p></section>
<section id="nickname" class="mb-4"><h2>Nickname(s)</h2><ul>
<li>Ling Dang (鈴鐺)</li><li>Ling Ling (鈴鈴)</li><li>Director (院長)</li><li>Rinny</li><li>S00 (S00)</li></ul></section>
<section id="affiliation" class="mb-4"><h2>Affiliation</h2><a href="x">Independent</a></section>
<section id="gender" class="mb-4"><h2>Gender</h2><a href="x">Female</a></section>
<section id="fanbase" class="mb-4"><h2>Fanbase</h2>Ling Qiu &amp; Ghost Ball</section>
<section id="status"><h2>Status</h2><p>Active<p></section>
<section id="birthday" class="mb-4"><h2>Birthday</h2>May 4</section>
<section id="description" class="mb-4"><h2>Description</h2><p>Her fanbase is called "Ling Qiu (靈球團)", meaning "Ghost Ball".</p></section>
<section id="links" class="mb-4"><h2>Links</h2><h3>Channels</h3>
<a href="https://www.twitch.tv/sample">t</a><a href="https://open.spotify.com/x">s</a>
<h3>Socials</h3><a href="https://x.com/sample">x</a></section>
"""


def test_profile_keeps_only_the_naming_fields():
    row = vnp.parse_profile("sample-rin", "tw", PROFILE)
    assert row["name"] == "Sample Rin" and row["original"] == "範例·鈴"
    assert row["nicknames"] == ["Ling Dang (鈴鐺)", "Ling Ling (鈴鈴)", "Director (院長)", "Rinny", "S00 (S00)"]
    assert row["fanbase_native"] == "靈球團" and row["platforms"] == ["twitch.tv"]
    assert "birthday" not in json.dumps(row).lower() and "May 4" not in json.dumps(row)


class _Words:
    common = {"院長", "院长"}

    def is_common(self, word, language):
        return word in self.common

    def zipf(self, word, language):
        return 4.0 if word in self.common else 0.0

    def is_compound(self, word, language):
        return word in ("小松鼠",)

    def inside_common_word(self, word, language):
        return word in ("メダ",)


def test_names_nicknames_and_fans_become_entries_in_both_scripts_and_risky_ones_are_held():
    pytest.importorskip("opencc")
    row = vnp.parse_profile("sample-rin", "tw", PROFILE)
    kept, held = vnp.filter_candidates(vnp.candidates([row]), _Words(), {"zh": set(), "ja": set()})
    by_source = {e["source"]: e for e in kept["zh"]}
    assert by_source["範例·鈴"]["en"] == by_source["範例鈴"]["en"] == by_source["范例铃"]["en"] == "Sample Rin"
    assert by_source["鈴鐺"]["en"] == by_source["铃铛"]["en"] == "Ling Dang"
    assert by_source["靈球團"]["en"] == "Ling Qiu (Sample Rin's fans)"
    assert by_source["鈴鐺"]["link"] == "https://hololist.net/sample-rin/"
    assert "she/her" in by_source["範例鈴"]["note"] and "Nickname of Sample Rin" in by_source["鈴鐺"]["note"]
    # An ordinary word and a doubled diminutive are held back; a nickname with no native-script
    # form has no source at all.
    reasons = {h[1]: h[4].split(" (")[0] for h in held}
    assert reasons["院長"] == "ordinary word" and reasons["鈴鈴"] == "generic nickname form"
    assert not any(e["source"] in ("Rinny", "S00") for e in kept["zh"])


def test_a_source_used_by_two_vtubers_is_held_unless_one_owns_it_as_a_name():
    pytest.importorskip("opencc")
    a = {**vnp.parse_profile("a", "jp", PROFILE), "original": "ふたりめ", "nicknames": ["Mochi (おもちこ)"]}
    b = {**vnp.parse_profile("b", "jp", PROFILE), "original": "おもちこ", "nicknames": ["Fu (ふたりめ)", "Mo (もちもち)"]}
    c = {**vnp.parse_profile("c", "jp", PROFILE), "original": "さんにんめ", "nicknames": ["Mo (もちもち)"]}
    kept, held = vnp.filter_candidates(vnp.candidates([a, b, c]), _Words(), {"zh": set(), "ja": set()})
    owners = {e["source"]: e["link"] for e in kept["ja"]}
    assert owners["おもちこ"].endswith("/b/") and owners["ふたりめ"].endswith("/a/")
    assert "もちもち" not in owners and any(h[1] == "もちもち" for h in held)


def test_a_vtuber_without_a_video_channel_is_left_out():
    pytest.importorskip("opencc")
    row = {**vnp.parse_profile("sample-rin", "tw", PROFILE), "platforms": []}
    assert vnp.candidates([row]) == []


def test_rebuild_keeps_hand_written_entries_and_applies_overrides(tmp_path, monkeypatch):
    pytest.importorskip("opencc")
    packs = tmp_path / "packs"
    packs.mkdir()
    for language in ("ja", "zh"):
        (packs / f"{language}-vtuber-names.json").write_text(json.dumps({
            "id": f"{language}-vtuber-names", "version": 3, "language": language, "title": "T",
            "description": "D", "entries": [{"source": "手寫", "en": "Hand", "category": "streamer"},
                                            {"source": "舊的", "en": "Old", "link": "https://hololist.net/old/"}]}),
            encoding="utf-8")
    (packs / "zh-common.json").write_text(json.dumps({
        "id": "zh-common", "version": 1, "language": "zh", "title": "T", "description": "D",
        "entries": [{"source": "鈴鐺", "en": "Common"}]}), encoding="utf-8")
    overrides = tmp_path / "overrides.json"
    overrides.write_text(json.dumps({"zh": {"靈球團": {"en": "Ghost Balls"}, "範例鈴": {"drop": True}}}),
                         encoding="utf-8")
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "profiles.jsonl").write_text(
        json.dumps(vnp.parse_profile("sample-rin", "tw", PROFILE), ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setattr(vnp, "PACK_DIR", packs)
    monkeypatch.setattr(vnp, "OVERRIDES", overrides)
    monkeypatch.setattr(vnp, "_Words", _Words)
    vnp.build(cache, tmp_path / "review.md")
    pack = json.loads((packs / "zh-vtuber-names.json").read_text(encoding="utf-8"))
    by_source = {e["source"]: e for e in pack["entries"]}
    assert pack["version"] == 4 and pack["entries"][0]["source"] == "手寫"
    assert "舊的" not in by_source and "範例鈴" not in by_source and "範例·鈴" in by_source
    assert by_source["靈球團"]["en"] == "Ghost Balls"
    # Another pack of the language already has 鈴鐺, so its entry stays the only one, in both scripts.
    assert "鈴鐺" not in by_source and "铃铛" not in by_source
    assert "院長" in (tmp_path / "review.md").read_text(encoding="utf-8")


def test_the_agency_spelling_wins_and_wikidata_adds_the_other_languages_names():
    pytest.importorskip("opencc")
    row = {**vnp.parse_profile("sample-rin", "jp", PROFILE), "original": "範例りん", "nicknames": []}
    official = [{"native": "範例りん", "en": "Hanrei Rin", "link": "https://agency.example/rin/", "agency": "Agency"},
                {"native": "別の人", "en": "Betsu", "link": "https://agency.example/betsu/", "agency": "Agency"}]
    wikidata = [{"qid": "Q1", "gender": "Female", "labels": {"en": ["Rin Hanrei"], "ja": ["範例りん"], "zh": ["范例铃"]}},
                {"qid": "Q2", "gender": "Male", "labels": {"en": ["Only Wiki"], "zh-hant": ["只在維基"]}},
                {"qid": "Q3", "gender": "", "labels": {"ja": ["英語なし"]}}]
    kept, _ = vnp.filter_candidates(vnp.candidates([row], official, wikidata), _Words(), {"zh": set(), "ja": set()})
    ja = {e["source"]: e for e in kept["ja"]}
    zh = {e["source"]: e for e in kept["zh"]}
    assert ja["範例りん"]["en"] == "Hanrei Rin" and ja["範例りん"]["link"] == "https://hololist.net/sample-rin/"
    assert ja["別の人"]["link"] == "https://agency.example/betsu/"
    # Wikidata's Chinese name for a Japanese VTuber Hololist knows keeps Hololist's link and the agency spelling.
    assert zh["范例铃"]["en"] == zh["範例鈴"]["en"] == "Hanrei Rin" and zh["范例铃"]["link"].startswith("https://hololist.net/")
    assert zh["只在維基"]["en"] == "Only Wiki" and "he/him" in zh["只在維基"]["note"]
    assert zh["只在維基"]["link"] == "https://www.wikidata.org/wiki/Q2"
    # No English label means no rendering to give, so the item adds nothing.
    assert "英語なし" not in ja


def test_wikidata_in_the_other_script_is_the_same_vtuber_not_a_rival():
    pytest.importorskip("opencc")
    row = vnp.parse_profile("sample-rin", "tw", PROFILE)
    wikidata = [{"qid": "Q9", "gender": "", "labels": {"en": ["Sample Rin"], "zh-hans": ["范例·铃"]}}]
    kept, held = vnp.filter_candidates(vnp.candidates([row], (), wikidata), _Words(), {"zh": set(), "ja": set()})
    assert {"範例鈴", "范例铃"} <= {e["source"] for e in kept["zh"]}
    assert not [h for h in held if h[1] in ("範例鈴", "范例铃")]


def test_a_wikidata_label_equal_to_someone_elses_nickname_is_not_that_vtuber():
    pytest.importorskip("opencc")
    a = {**vnp.parse_profile("a", "jp", PROFILE), "original": "はじめの人", "nicknames": ["Mochi (おもちもち)"]}
    wikidata = [{"qid": "Q7", "gender": "", "labels": {"en": ["Other Mochi"], "ja": ["おもちもち"], "zh": ["另一个名字"]}}]
    kept, _ = vnp.filter_candidates(vnp.candidates([a], (), wikidata), _Words(), {"zh": set(), "ja": set()})
    zh = {e["source"]: e for e in kept["zh"]}
    assert zh["另一个名字"]["en"] == "Other Mochi" and zh["另一个名字"]["link"].endswith("/Q7")
    # Both claim おもちもち: one as a nickname, one as a name, so the name owner keeps it.
    assert {e["source"]: e for e in kept["ja"]}["おもちもち"]["en"] == "Other Mochi"


def test_a_name_listed_twice_by_its_owner_still_beats_a_rivals_nickname():
    pytest.importorskip("opencc")
    a = {**vnp.parse_profile("a", "tw", PROFILE), "nicknames": []}
    b = {**vnp.parse_profile("b", "tw", PROFILE), "original": "別人名字", "nicknames": ["Rin (範例鈴)"]}
    wikidata = [{"qid": "Q1", "gender": "", "labels": {"en": ["Sample Rin"], "zh-hant": ["範例鈴"]}}]
    kept, _ = vnp.filter_candidates(vnp.candidates([a, b], (), wikidata), _Words(), {"zh": set(), "ja": set()})
    assert {e["source"]: e for e in kept["zh"]}["範例鈴"]["link"].endswith("/a/")


def test_a_nickname_holding_another_packs_term_is_held_but_an_own_name_keeps_it():
    pytest.importorskip("opencc")
    row = {**vnp.parse_profile("a", "jp", PROFILE), "original": "姫森りん",
           "nicknames": ["Rin-chan (りんりんちゃん)", "Danna (旦那様)", "Waka (若様だよ)"]}
    kept, held = vnp.filter_candidates(vnp.candidates([row]), _Words(), {"zh": set(), "ja": set()},
                                       {"ja": {"ちゃん", "様", "姫"}})
    reasons = {h[1]: h[4] for h in held}
    assert reasons["りんりんちゃん"] == "holds another pack's term ちゃん" and reasons["旦那様"].endswith("様")
    # A one-character term counts only at the end, and a VTuber's own name is the name, not the word.
    assert {"姫森りん", "若様だよ"} <= {e["source"] for e in kept["ja"]}


def test_short_hiragana_names_need_an_agency_or_wikidata_listing():
    pytest.importorskip("opencc")
    row = {**vnp.parse_profile("a", "jp", PROFILE), "original": "はお", "nicknames": ["Hino (ひの)"]}
    wikidata = [{"qid": "Q3", "gender": "", "labels": {"en": ["Hino"], "ja": ["ひの"]}}]
    kept, held = vnp.filter_candidates(vnp.candidates([row], (), wikidata), _Words(), {"zh": set(), "ja": set()})
    assert ("はお", "short hiragana name") in [(h[1], h[4]) for h in held]
    assert "ひの" in {e["source"] for e in kept["ja"]}


def test_a_short_name_inside_a_common_word_is_held():
    pytest.importorskip("opencc")
    row = {**vnp.parse_profile("a", "jp", PROFILE), "original": "メダ", "nicknames": []}
    _, held = vnp.filter_candidates(vnp.candidates([row]), _Words(), {"zh": set(), "ja": set()})
    assert ("メダ", "part of a common word") in [(h[1], h[4]) for h in held]


def test_the_real_word_lists_find_names_inside_common_words():
    pytest.importorskip("wordfreq")
    pytest.importorskip("opencc")
    words = vnp._Words()
    assert words.inside_common_word("メダ", "ja") and words.inside_common_word("貓頭", "zh")
    assert not words.inside_common_word("マイロ", "ja")


def test_wikidata_only_names_holding_another_packs_term_are_held():
    pytest.importorskip("opencc")
    wikidata = [{"qid": "Q5", "gender": "", "labels": {"en": ["Young Master"], "zh-hans": ["本少爷"]}}]
    _, held = vnp.filter_candidates(vnp.candidates([], (), wikidata), _Words(), {"zh": set(), "ja": set()},
                                    {"zh": {"少爷"}})
    assert ("本少爷", "holds another pack's term 少爷") in [(h[1], h[4]) for h in held]


def test_badges_hidden_characters_brackets_and_risky_nicknames():
    pytest.importorskip("opencc")
    row = {**vnp.parse_profile("a", "tw", PROFILE), "original": "\u200b範例·鈴",
           "nicknames": ["Wang Xiao Ming (王小明)", "Friend (友人A（えー）)"],
           "fanbase": "Squirrels", "fanbase_native": "小松鼠"}
    official = [{"native": "範例鈴", "en": "[Alum] Sample Rin", "link": "https://example.com/t/", "agency": "A"}]
    kept, held = vnp.filter_candidates(vnp.candidates([row], official), _Words(), {"zh": set(), "ja": set()})
    by_source = {e["source"]: e for e in kept["zh"]}
    assert by_source["範例·鈴"]["en"] == by_source["範例鈴"]["en"] == "Sample Rin"
    reasons = {h[1]: h[4] for h in held}
    assert reasons["王小明"] == "may be a personal name" and reasons["友人A（えー）"] == "has brackets"
    assert reasons["小松鼠"] == "fan name made of ordinary words"


def test_a_failed_list_page_does_not_wipe_cached_profiles(tmp_path, monkeypatch):
    cached = {"slug": "old-one", "category": "jp", "fetched": "2026-11-27"}
    (tmp_path / "profiles.jsonl").write_text(json.dumps(cached) + "\n", encoding="utf-8")
    monkeypatch.setattr(vnp, "_category_slugs", lambda session, category: ([], category != "jp"))
    vnp.fetch(tmp_path, refresh_max=0)
    assert "old-one" in vnp.load_cache(tmp_path)
    monkeypatch.setattr(vnp, "_category_slugs", lambda session, category: ([], True))
    vnp.fetch(tmp_path, refresh_max=0)
    assert vnp.load_cache(tmp_path) == {}


def test_a_build_that_the_app_would_refuse_fails_instead_of_writing(tmp_path, monkeypatch):
    pytest.importorskip("opencc")
    packs = tmp_path / "packs"
    packs.mkdir()
    for language in ("ja", "zh"):
        (packs / f"{language}-vtuber-names.json").write_text(json.dumps({
            "id": f"{language}-vtuber-names", "version": 1, "language": language, "title": "T",
            "description": "D", "entries": [{"source": "手寫", "en": "Hand"}]}), encoding="utf-8")
    long_name = {**vnp.parse_profile("sample-rin", "tw", PROFILE), "name": "N" * 300, "nicknames": []}
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "profiles.jsonl").write_text(json.dumps(long_name, ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setattr(vnp, "PACK_DIR", packs)
    monkeypatch.setattr(vnp, "_Words", _Words)
    before = {lang: (packs / f"{lang}-vtuber-names.json").read_text(encoding="utf-8") for lang in ("ja", "zh")}
    good = {**long_name, "slug": "ja-one", "category": "jp", "name": "Rin", "original": "範例りん"}
    (cache / "profiles.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in (good, long_name)),
                                          encoding="utf-8")
    with pytest.raises(vnp.language_packs.PackError):
        vnp.build(cache, None)
    # The Japanese pack was fine, but a failed build writes neither.
    assert {lang: (packs / f"{lang}-vtuber-names.json").read_text(encoding="utf-8") for lang in before} == before


def test_a_build_that_would_drop_most_generated_entries_needs_force(tmp_path, monkeypatch):
    pytest.importorskip("opencc")
    packs = tmp_path / "packs"
    packs.mkdir()
    old = [{"source": f"名字{i}", "en": f"N{i}", "link": "https://hololist.net/x/"} for i in range(vnp.GUARDED_FROM)]
    for language in ("ja", "zh"):
        (packs / f"{language}-vtuber-names.json").write_text(json.dumps({
            "id": f"{language}-vtuber-names", "version": 1, "language": language, "title": "T",
            "description": "D", "entries": [{"source": "手寫", "en": "Hand"}] + old}), encoding="utf-8")
    cache = tmp_path / "empty-cache"
    cache.mkdir()
    monkeypatch.setattr(vnp, "PACK_DIR", packs)
    monkeypatch.setattr(vnp, "_Words", _Words)
    def sources():
        return [e["source"] for e in json.loads((packs / "zh-vtuber-names.json").read_text(encoding="utf-8"))["entries"]]
    with pytest.raises(SystemExit, match="--force"):
        vnp.build(cache, None)
    assert "名字0" in sources()
    vnp.build(cache, None, force=True)
    assert sources() == ["手寫"]


def test_a_failed_agency_page_keeps_that_agencys_last_list(tmp_path, monkeypatch):
    old = [{"native": "甲", "en": "A", "link": "https://example.com/a", "agency": "hololive production"},
           {"native": "乙", "en": "B", "link": "https://example.com/b", "agency": "NIJISANJI"}]
    (tmp_path / "official.json").write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    fresh = {"native": "丙", "en": "C", "link": "https://example.com/c", "agency": "NIJISANJI"}
    monkeypatch.setattr(vnp, "_official", lambda session: [fresh])
    monkeypatch.setattr(vnp, "_wikidata", lambda session: [])
    pytest.importorskip("requests")
    vnp.fetch_reference(tmp_path)
    saved = json.loads((tmp_path / "official.json").read_text(encoding="utf-8"))
    assert {o["native"] for o in saved} == {"甲", "丙"}


def test_each_of_several_fan_names_is_an_entry_and_a_two_character_one_is_held():
    pytest.importorskip("opencc")
    row = {**vnp.parse_profile("sample-rin", "jp", PROFILE), "original": "範例りん", "nicknames": [],
           "fanbase": "Hakamori & Rinners", "fanbase_native": "墓守 & りんなーず"}
    kept, held = vnp.filter_candidates(vnp.candidates([row]), _Words(), {"zh": set(), "ja": set()})
    assert {e["source"]: e["en"] for e in kept["ja"]}["りんなーず"] == "Rinners (Sample Rin's fans)"
    assert ("墓守", "two-character fan name") in [(h[1], h[4]) for h in held]


def test_excluded_terms_never_reach_the_pack_or_the_review_file(tmp_path, monkeypatch):
    pytest.importorskip("opencc")
    packs = tmp_path / "packs"
    packs.mkdir()
    for language in ("ja", "zh"):
        (packs / f"{language}-vtuber-names.json").write_text(json.dumps({
            "id": f"{language}-vtuber-names", "version": 1, "language": language, "title": "T",
            "description": "D", "entries": [{"source": "手寫", "en": "Hand"}]}), encoding="utf-8")
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "profiles.jsonl").write_text(
        json.dumps(vnp.parse_profile("sample-rin", "tw", PROFILE), ensure_ascii=False) + "\n", encoding="utf-8")
    exclude = tmp_path / "private.txt"
    exclude.write_text("# names not to publish\nsample rin\n", encoding="utf-8")
    monkeypatch.setattr(vnp, "PACK_DIR", packs)
    monkeypatch.setattr(vnp, "_Words", _Words)
    vnp.build(cache, tmp_path / "review.md", exclude)
    text = (packs / "zh-vtuber-names.json").read_text(encoding="utf-8")
    assert "Sample Rin" not in text and "Ling Dang" not in text and "手寫" in text
    assert "sample-rin" not in (tmp_path / "review.md").read_text(encoding="utf-8")


def test_an_excluded_vtubers_wikidata_label_in_the_other_script_is_left_out(tmp_path, monkeypatch):
    pytest.importorskip("opencc")
    packs = tmp_path / "packs"
    packs.mkdir()
    for language in ("ja", "zh"):
        (packs / f"{language}-vtuber-names.json").write_text(json.dumps({
            "id": f"{language}-vtuber-names", "version": 1, "language": language, "title": "T",
            "description": "D", "entries": [{"source": "手寫", "en": "Hand"}]}), encoding="utf-8")
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "profiles.jsonl").write_text(
        json.dumps(vnp.parse_profile("sample-rin", "tw", PROFILE), ensure_ascii=False) + "\n", encoding="utf-8")
    (cache / "wikidata.json").write_text(json.dumps(
        [{"qid": "Q9", "gender": "", "labels": {"en": ["Rin Sample"], "zh-hans": ["范例铃"]}}], ensure_ascii=False),
        encoding="utf-8")
    exclude = tmp_path / "private.txt"
    exclude.write_text("Sample Rin\n範例鈴\n", encoding="utf-8")
    monkeypatch.setattr(vnp, "PACK_DIR", packs)
    monkeypatch.setattr(vnp, "_Words", _Words)
    vnp.build(cache, None, exclude)
    text = (packs / "zh-vtuber-names.json").read_text(encoding="utf-8")
    assert "范例铃" not in text and "Rin Sample" not in text and "Q9" not in text
