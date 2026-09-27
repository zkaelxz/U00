"""
tests/test_line_tools.py -- line_tools.py's per-line tools.

Step 30: explain_translation()'s glossary-relevance matcher used to check
only term_original against the line's source text (`t.get("term_original",
"") in zh`), so a line containing only a recorded alias/alt-spelling of a
term -- not the canonical term_original -- silently missed that term's
glossary guidance.
"""
import line_tools


class _FakeEngine:
    supports_reference = True


def test_explain_translation_surfaces_a_term_matched_only_by_alias(monkeypatch):
    captured = {}

    def _fake_call_llm_json(engine, prompt, max_tokens=800, fallback=""):
        captured["prompt"] = prompt
        return "explanation"

    monkeypatch.setattr(line_tools, "call_llm_json", _fake_call_llm_json)

    terms = [{"term_original": "沈清疑", "term_translation": "Shen Qingyi",
              "aliases": "沈清儀|Shen Qing Yi"}]
    # The line uses only the alias 沈清儀, never the canonical 沈清疑.
    line_tools.explain_translation("沈清儀来了。", "Shen Qingyi is here.", _FakeEngine(),
                                   glossary_terms=terms)

    assert "Glossary terms in play" in captured["prompt"]
    assert "沈清疑 → Shen Qingyi" in captured["prompt"]


def test_explain_translation_skips_terms_with_no_match_at_all(monkeypatch):
    captured = {}

    def _fake_call_llm_json(engine, prompt, max_tokens=800, fallback=""):
        captured["prompt"] = prompt
        return "explanation"

    monkeypatch.setattr(line_tools, "call_llm_json", _fake_call_llm_json)

    terms = [{"term_original": "沈清疑", "term_translation": "Shen Qingyi", "aliases": "沈清儀"}]
    line_tools.explain_translation("别的句子。", "A different sentence.", _FakeEngine(),
                                   glossary_terms=terms)

    assert "Glossary terms in play" not in captured["prompt"]
