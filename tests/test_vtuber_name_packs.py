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
<li>Ling Ling (鈴鈴)</li><li>Director (院長)</li><li>Rinny</li><li>S00 (S00)</li></ul></section>
<section id="affiliation" class="mb-4"><h2>Affiliation</h2><a href="x">Independent</a></section>
<section id="gender" class="mb-4"><h2>Gender</h2><a href="x">Female</a></section>
<section id="fanbase" class="mb-4"><h2>Fanbase</h2>Ling Qiu &amp; Ghost Ball</section>
<section id="status"><h2>Status</h2><p>Active<p></section>
<section id="birthday" class="mb-4"><h2>Birthday</h2>May 4</section>
<section id="description" class="mb-4"><h2>Description</h2><p>Her fanbase is called "Ling Qiu (靈球)", meaning "Ghost Ball".</p></section>
<section id="links" class="mb-4"><h2>Links</h2><h3>Channels</h3>
<a href="https://www.twitch.tv/sample">t</a><a href="https://open.spotify.com/x">s</a>
<h3>Socials</h3><a href="https://x.com/sample">x</a></section>
"""


def test_profile_keeps_only_the_naming_fields():
    row = vnp.parse_profile("sample-rin", "tw", PROFILE)
    assert row["name"] == "Sample Rin" and row["original"] == "範例·鈴"
    assert row["nicknames"] == ["Ling Ling (鈴鈴)", "Director (院長)", "Rinny", "S00 (S00)"]
    assert row["fanbase_native"] == "靈球" and row["platforms"] == ["twitch.tv"]
    assert "birthday" not in json.dumps(row).lower() and "May 4" not in json.dumps(row)


class _Words:
    common = {"院長", "院长"}

    def is_common(self, word, language):
        return word in self.common

    def zipf(self, word, language):
        return 4.0 if word in self.common else 0.0


def test_names_nicknames_and_fans_become_entries_in_both_scripts_and_risky_ones_are_held():
    pytest.importorskip("opencc")
    row = vnp.parse_profile("sample-rin", "tw", PROFILE)
    kept, held = vnp.filter_candidates(vnp.candidates([row]), _Words(), {"zh": set(), "ja": set()})
    by_source = {e["source"]: e for e in kept["zh"]}
    assert by_source["範例·鈴"]["en"] == by_source["範例鈴"]["en"] == by_source["范例铃"]["en"] == "Sample Rin"
    assert by_source["鈴鈴"]["en"] == by_source["铃铃"]["en"] == "Ling Ling"
    assert by_source["靈球"]["en"] == "Ling Qiu (Sample Rin's fans)"
    assert by_source["鈴鈴"]["link"] == "https://hololist.net/sample-rin/"
    assert "she/her" in by_source["範例鈴"]["note"] and "Nickname of Sample Rin" in by_source["鈴鈴"]["note"]
    # An ordinary word is held back; a nickname with no native-script form has no source at all.
    assert ("院長", "ordinary word") in [(h[1], h[4].split(" (")[0]) for h in held]
    assert not any(e["source"] in ("Rinny", "S00") for e in kept["zh"])


def test_a_source_used_by_two_vtubers_is_held_unless_one_owns_it_as_a_name():
    pytest.importorskip("opencc")
    a = {**vnp.parse_profile("a", "jp", PROFILE), "original": "ふたりめ", "nicknames": ["Mochi (おもち)"]}
    b = {**vnp.parse_profile("b", "jp", PROFILE), "original": "おもち", "nicknames": ["Fu (ふたりめ)", "Mo (もちもち)"]}
    c = {**vnp.parse_profile("c", "jp", PROFILE), "original": "さんにんめ", "nicknames": ["Mo (もちもち)"]}
    kept, held = vnp.filter_candidates(vnp.candidates([a, b, c]), _Words(), {"zh": set(), "ja": set()})
    owners = {e["source"]: e["link"] for e in kept["ja"]}
    assert owners["おもち"].endswith("/b/") and owners["ふたりめ"].endswith("/a/")
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
        "entries": [{"source": "鈴鈴", "en": "Common"}]}), encoding="utf-8")
    overrides = tmp_path / "overrides.json"
    overrides.write_text(json.dumps({"zh": {"靈球": {"en": "Ghost Balls"}, "範例鈴": {"drop": True}}}),
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
    assert by_source["靈球"]["en"] == "Ghost Balls"
    # Another pack of the language already has 鈴鈴, so its entry stays the only one.
    assert "鈴鈴" not in by_source and "铃铃" in by_source
    assert "院長" in (tmp_path / "review.md").read_text(encoding="utf-8")
