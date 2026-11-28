"""tools/planted_mistake_drill.py: planting is repeatable and only touches lines that can carry the mistake,
each check is scored on caught and false flags, and the report holds counts, never line text. No Ollama."""
import json

from tools import planted_mistake_drill as drill

ZH = ["他走了", "林默走进房间", "她笑了", "我们明天见", "你好吗朋友", "林默说他累了", "下雨了", "我买了3个苹果"]
GOLD = ["He left", "Lin Mo walked into the room", "She smiled", "See you tomorrow",
        "How are you, friend", "Lin Mo said he was tired", "It is raining", "I bought 3 apples"]
TERMS = [{"term_original": "林默", "term_translation": "Lin Mo"}]


def test_planting_is_repeatable_and_each_kind_lands_where_it_can():
    en, planted = drill.plant(ZH, GOLD, TERMS, rate=0.1, seed=3)
    again, planted_again = drill.plant(ZH, GOLD, TERMS, rate=0.1, seed=3)
    assert (en, planted) == (again, planted_again)
    assert sorted(planted.values()) == sorted(drill.KINDS)
    for i, kind in planted.items():
        if kind == "shift":
            assert en[i] == GOLD[i + 1]
        elif kind == "number":
            assert i == len(GOLD) - 1 and en[i] == "I bought 4 apples"
        elif kind == "glossary":
            assert "Lin Mo" not in en[i] and "someone" in en[i]
        else:
            assert en[i] == drill._swap_pronouns(GOLD[i]) != GOLD[i]
    untouched = [i for i in range(len(GOLD)) if i not in planted]
    assert all(en[i] == GOLD[i] for i in untouched)


def test_auto_qc_and_glossary_catch_their_own_kind_without_false_flags():
    en, planted = drill.plant(ZH, GOLD, TERMS, rate=0.1, seed=3)
    result = drill.score(planted, drill.run_checks(ZH, en, TERMS, None), len(ZH))
    assert result["checks"]["source_match"] is None
    assert result["checks"]["auto_qc"]["caught"]["number"] == 1
    assert result["checks"]["glossary"]["caught"]["glossary"] == 1
    assert result["checks"]["glossary"]["false_flags"] == 0
    assert result["checks"]["any"]["caught"]["number"] == 1


def test_source_match_is_scored_from_the_embedder():
    en, planted = drill.plant(ZH, GOLD, TERMS, rate=0.1, seed=3)
    shifted = next(i for i, k in planted.items() if k == "shift")

    def embed(texts):
        # Every source and its own gold English point the same way; anything else is orthogonal.
        half = len(texts) // 2
        out = [[1.0, 0.0]] * half
        return out + [[1.0, 0.0] if t in GOLD and GOLD.index(t) == k and k != shifted else [0.0, 1.0]
                      for k, t in enumerate(texts[half:])]

    result = drill.score(planted, drill.run_checks(ZH, en, TERMS, embed), len(ZH))
    assert result["checks"]["source_match"]["caught"]["shift"] == 1


def test_the_report_and_json_hold_counts_only(tmp_path, capsys):
    src = tmp_path / "s.srt"
    gold = tmp_path / "g.srt"
    cue = "{n}\n00:00:{s:02d},000 --> 00:00:{s:02d},500\n{t}\n\n"
    src.write_text("".join(cue.format(n=i + 1, s=i, t=t) for i, t in enumerate(ZH)), encoding="utf-8")
    gold.write_text("".join(cue.format(n=i + 1, s=i, t=t) for i, t in enumerate(GOLD)), encoding="utf-8")
    glossary = tmp_path / "g.json"
    glossary.write_text(json.dumps([{"original": "林默", "translation": "Lin Mo"}]), encoding="utf-8")
    out = tmp_path / "r.json"
    assert drill.main([str(src), str(gold), "--language", "zh", "--glossary", str(glossary), "--no-embed",
                       "--rate", "0.1", "--out", str(out)]) == 0
    printed = capsys.readouterr().out + out.read_text(encoding="utf-8")
    assert "| glossary |" in printed and "not run" in printed
    for text in ZH + GOLD:
        assert text not in printed


def test_a_gold_file_that_does_not_match_line_for_line_is_refused(tmp_path, capsys):
    src = tmp_path / "s.srt"
    src.write_text("1\n00:00:01,000 --> 00:00:02,000\n下雨了\n", encoding="utf-8")
    gold = tmp_path / "g.srt"
    gold.write_text("1\n00:00:01,000 --> 00:00:02,000\nRain\n\n2\n00:00:03,000 --> 00:00:04,000\nMore\n", encoding="utf-8")
    assert drill.main([str(src), str(gold), "--language", "zh", "--no-embed"]) == 2
    assert "must match line for line" in capsys.readouterr().out


def test_an_embedder_that_cannot_answer_is_reported_as_not_run():
    en, planted = drill.plant(ZH, GOLD, TERMS, rate=0.1, seed=3)
    for embed in (lambda texts: None, lambda texts: [[1.0, 0.0]], lambda texts: [None] * len(texts)):
        result = drill.score(planted, drill.run_checks(ZH, en, TERMS, embed), len(ZH))
        assert result["checks"]["source_match"] is None
        assert "not run" in drill.format_report(result, 3)
