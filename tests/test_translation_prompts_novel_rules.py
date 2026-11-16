"""Novel-mode prompt rules that stop unit conversion, name drift and gender slips."""
from engine_backends import medium_prompts
import glossary_extract


def test_novel_rules_cover_units_names_and_gender():
    rules = medium_prompts.length_rule("novel")
    assert "never convert to feet" in rules
    assert "never alternate a pinyin form" in rules.replace("and never alternate a ", "never alternate a ")
    assert "gender comes from the source" in rules


def test_other_media_do_not_get_the_novel_rules():
    for kind in ("subtitle", "stream", "audio"):
        assert "feet" not in medium_prompts.length_rule(kind)


def test_novel_glossary_prompt_asks_for_pronouns():
    seen = []

    class Eng:
        supports_reference = True

    def fake_llm(engine, prompt, **kw):
        seen.append(prompt)
        return "[]"

    orig = glossary_extract.call_llm_json
    glossary_extract.call_llm_json = fake_llm
    try:
        glossary_extract.extract_glossary_from_novel("她走了。" * 10, Eng())
    finally:
        glossary_extract.call_llm_json = orig
    assert seen and "start the reason with the character's pronouns" in seen[0]


def test_novel_rules_ask_for_original_english_prose():
    assert "translate the scene, not the sentences" in medium_prompts.length_rule("novel")
