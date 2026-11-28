"""Tests for engine pricing, cost estimates, cost caps and the cache-friendly Claude prompt."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from engine_backends import translate_pipeline
from engine_backends import shared as engine_shared
from engine_backends import gemini as engine_gemini
from engine_backends import claude as engine_claude
from engine_backends import prompts as engine_prompts
from engine_backends import pricing as engine_pricing
from core import Line
from tests.http_fakes import StreamedBody, patch_post


class TestEstimateCost:
    def test_known_model_computes_nonzero_cost(self):
        cost = engine_pricing.estimate_cost("claude-sonnet-5", 1_000_000, 500_000)
        assert cost > 0

    def test_unknown_model_returns_zero_not_crash(self):
        cost = engine_pricing.estimate_cost("some-made-up-model-xyz", 1000, 500)
        assert cost == 0.0

    def test_zero_tokens_zero_cost(self):
        assert engine_pricing.estimate_cost("claude-sonnet-5", 0, 0) == 0.0

    def test_gemini_models_have_pricing(self):
        for model in engine_pricing.GEMINI_MODELS:
            cost = engine_pricing.estimate_cost(model, 1_000_000, 1_000_000)
            assert cost > 0, f"{model} should have real pricing, not silently cost $0"

    def test_cost_scales_with_token_count(self):
        small = engine_pricing.estimate_cost("claude-sonnet-5", 1000, 500)
        large = engine_pricing.estimate_cost("claude-sonnet-5", 10000, 5000)
        assert large > small
        assert abs(large - small * 10) < 0.0001  # linear scaling


class TestClaude55Pricing:
    def test_new_ids_use_their_own_table_rows(self):
        assert engine_pricing.estimate_cost("claude-sonnet-5-5", 1_000_000, 500_000) == pytest.approx(2.0 + 5.0)
        assert engine_pricing.estimate_cost("claude-opus-5-5", 1_000_000, 500_000) == pytest.approx(4.0 + 10.0)

    def test_opus_5_5_costs_less_than_opus_4_8(self):
        assert (engine_pricing.estimate_cost("claude-opus-5-5", 1_000_000, 1_000_000)
                < engine_pricing.estimate_cost("claude-opus-4-8", 1_000_000, 1_000_000))

    def test_sonnet_5_5_costs_the_same_as_sonnet_5(self):
        assert (engine_pricing.estimate_cost("claude-sonnet-5-5", 1_000_000, 1_000_000)
                == engine_pricing.estimate_cost("claude-sonnet-5", 1_000_000, 1_000_000))

    def test_cache_read_is_never_estimated_below_the_published_rate(self):
        # Anthropic bills 5.5 cache reads at 5% of input; the flat 10% factor
        # must stay at or above that so cost caps are not undercut.
        full = engine_pricing.estimate_cost("claude-opus-5-5", 1_000_000, 0)
        cached = engine_pricing.estimate_cost("claude-opus-5-5", 1_000_000, 0, cache_read_tokens=1_000_000)
        assert cached >= full * 0.05

    def test_an_unknown_claude_id_is_still_costed_at_its_tier_ceiling(self):
        assert engine_pricing.estimate_cost("claude-opus-9-9", 1_000_000, 0) == pytest.approx(5.0)


class TestEstimateCostForEngine:
    """Step 1d item 5: cost shows $0 for a free-tier Gemini call, not the
    paid per-token rate its model name would normally look up."""

    def test_free_tier_gemini_is_zero_regardless_of_tokens(self):
        engine = engine_gemini.GeminiEngine("fake-key", free_tier=True)
        assert engine_pricing.estimate_cost_for_engine(engine, 1_000_000, 1_000_000) == 0.0

    def test_paid_gemini_matches_plain_estimate_cost(self):
        engine = engine_gemini.GeminiEngine("fake-key", model="gemini-flash-lite-latest", free_tier=False)
        expected = engine_pricing.estimate_cost("gemini-flash-lite-latest", 1000, 500)
        assert engine_pricing.estimate_cost_for_engine(engine, 1000, 500) == expected

    def test_an_engine_with_no_free_tier_attribute_falls_back_normally(self):
        engine = engine_claude.ClaudeEngine.__new__(engine_claude.ClaudeEngine)  # no free_tier attr at all
        engine.model = "claude-sonnet-5"
        expected = engine_pricing.estimate_cost("claude-sonnet-5", 1000, 500)
        assert engine_pricing.estimate_cost_for_engine(engine, 1000, 500) == expected


class TestEstimateTranslationTokens:
    def test_every_batch_adds_the_instructions_again(self):
        lines = ["你好"] * 100
        one_request, _ = engine_pricing.estimate_translation_tokens(lines)
        five_batches, _ = engine_pricing.estimate_translation_tokens(lines, batch_size=20)
        fixed = one_request - sum(len(z) for z in lines)  # one token per CJK character
        assert five_batches - one_request == 4 * fixed
        assert fixed > 300  # the measured instruction text, not just the old flat allowance

    def test_cjk_source_counts_a_token_per_character(self):
        cjk, _ = engine_pricing.estimate_translation_tokens(["你好世界" * 25])
        latin, _ = engine_pricing.estimate_translation_tokens(["abcd" * 25])
        assert cjk - latin == 100 - int(100 / 3.5)

    def test_gemini_reasoning_tokens_count_as_output(self):
        usage = engine_shared.gemini_usage(
            {"promptTokenCount": 10, "candidatesTokenCount": 5, "thoughtsTokenCount": 40})
        assert usage["output_tokens"] == 45


# ---------------------------------------------------------------------------
# Step 9: prompt caching -- the stable part of the prompt must be
# byte-identical across every batch of one drama, with everything that
# changes per batch after it.
# ---------------------------------------------------------------------------

class _FakeClaudeUsage:
    def __init__(self, input_tokens=100, output_tokens=20, cache_read=0, cache_write=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = cache_read
        self.cache_creation_input_tokens = cache_write


class _FakeClaudeMessages:
    """Records every messages.create call and answers every requested id
    with "EN" plus that line's source, so no batch ever needs a retry."""
    def __init__(self, usage=None):
        self.calls = []
        self.usage = usage or _FakeClaudeUsage()

    def create(self, **kwargs):
        import json as _json
        import re as _re
        self.calls.append(kwargs)
        user = kwargs["messages"][0]["content"]
        numbered = user.split("Translate these lines:\n\n", 1)[1]
        asked = _re.findall(r"^(\d+)\. (.*)$", numbered, flags=_re.M)
        text = _json.dumps({i: f"EN{zh}" for i, zh in asked})

        class _Block:
            type = "text"
        block = _Block()
        block.text = text

        class _Resp(StreamedBody):
            pass
        resp = _Resp()
        resp.content = [block]
        resp.usage = self.usage
        return resp


def _claude_engine_with_fake_client(usage=None):
    engine = engine_claude.ClaudeEngine("sk-ant-fake")
    engine.client = type("C", (), {})()
    engine.client.messages = _FakeClaudeMessages(usage)
    return engine


def _six_lines():
    return [Line(idx=i, start=i, end=i + 1, zh=f"第{i}句", id=100 + i) for i in range(6)]


class TestStablePromptPrefix:
    def _run_claude(self, **kw):
        engine = _claude_engine_with_fake_client()
        lines = _six_lines()
        translate_pipeline.translate_lines_with_engine(
            lines, engine, {"title_en": "Drama"}, batch_size=2,
            novel_reference="Reference novel text.", glossary_terms=[
                {"term_original": "苏杉", "term_translation": "Su Shan"}],
            style_guidelines="Keep it warm.", **kw)
        return engine.client.messages.calls, lines

    def test_claude_system_blocks_are_byte_identical_across_batches(self):
        calls, lines = self._run_claude()
        assert len(calls) == 3
        systems = [repr(c["system"]) for c in calls]
        assert systems[0] == systems[1] == systems[2]
        assert [ln.en for ln in lines] == [f"EN第{i}句" for i in range(6)]

    def test_per_batch_context_lives_in_the_user_message_only(self):
        calls, _ = self._run_claude()
        # Batch 2 sees batch 1's translations (look-back) and batch 3's
        # source (look-ahead) -- both must be in the user message, and
        # neither may leak into the cached system prompt.
        second_user = calls[1]["messages"][0]["content"]
        assert "第0句 -> EN第0句" in second_user
        assert "第4句" in second_user.split("Translate these lines:")[0]
        system_text = "".join(b["text"] for b in calls[1]["system"])
        assert "第0句" not in system_text and "第4句" not in system_text
        assert calls[0]["messages"][0]["content"] != calls[1]["messages"][0]["content"]

    def test_the_last_stable_block_carries_cache_control(self):
        calls, _ = self._run_claude()
        system = calls[0]["system"]
        assert system[-1].get("cache_control") == {"type": "ephemeral"}
        assert "Reference novel text." in system[-1]["text"]
        assert all("cache_control" not in b for b in system[:-1])

    def test_a_cut_down_novel_leaves_the_breakpoint_on_the_instructions(self):
        filler = "\n\n".join(f"Paragraph {i} about nothing much at all." * 20 for i in range(40))
        novel = f"Su Shan opened the gate.\n\n{filler}\n\nLin Wan closed the book."
        lines = [Line(idx=i, start=i, end=i + 1, zh=zh, id=100 + i)
                 for i, zh in enumerate(["苏杉来了", "苏杉走了", "林婉来了", "林婉走了"])]
        engine = _claude_engine_with_fake_client()
        translate_pipeline.translate_lines_with_engine(
            lines, engine, {"title_en": "Drama"}, batch_size=2, novel_reference=novel,
            glossary_terms=[{"term_original": "苏杉", "term_translation": "Su Shan"},
                            {"term_original": "林婉", "term_translation": "Lin Wan"}])
        # The fake never uses the glossary names, so each batch is followed by its re-ask.
        calls = engine.client.messages.calls
        first, second = (calls[0]["system"], calls[2]["system"])
        assert first[1]["text"] != second[1]["text"]   # per-batch excerpts
        assert repr(first[0]) == repr(second[0])
        assert first[0]["cache_control"] == {"type": "ephemeral"}
        assert all("cache_control" not in s[1] for s in (first, second))

    def test_without_a_novel_reference_the_instructions_block_is_cached(self):
        blocks = engine_prompts.build_claude_system_blocks({"drama_meta": {}})
        assert len(blocks) == 1
        assert blocks[0]["cache_control"] == {"type": "ephemeral"}

    def test_gemini_system_instruction_is_byte_identical_across_batches(self, monkeypatch):
        import json as _json
        import re as _re
        bodies = []

        class FakeResponse(StreamedBody):
            def __init__(self, body):
                self.body = body

            def raise_for_status(self):
                pass

            def json(self):
                user = self.body["contents"][0]["parts"][0]["text"]
                ids = _re.findall(r"^(\d+)\. ", user.split("Translate these lines:\n\n", 1)[1],
                                  flags=_re.M)
                return {"candidates": [{"content": {"parts": [
                    {"text": _json.dumps({i: "x" for i in ids})}]}}]}

        def fake_post(url, headers=None, json=None, timeout=None, stream=None):
            bodies.append(json)
            return FakeResponse(json)

        patch_post(monkeypatch, fake_post)
        translate_pipeline.translate_lines_with_engine(_six_lines(), engine_gemini.GeminiEngine("k"), {}, batch_size=2,
                                       novel_reference="Ref.")
        assert len(bodies) == 3
        assert bodies[0]["systemInstruction"] == bodies[1]["systemInstruction"] == \
            bodies[2]["systemInstruction"]


class TestBoundedNovelReference:
    """Step 50: build_stable_prompt() used to append context['novel_reference']
    to every batch's prompt whole and unbounded, however long the reference
    novel was -- crowding out instructions/dialogue, inflating cost, or
    exceeding a provider's context limit outright. It's now selected per
    batch by _select_relevant_novel_passages(), bounded to
    engine_prompts.NOVEL_REFERENCE_BUDGET_CHARS, and scored for relevance against the
    batch's own speaker names / glossary hits rather than always sending the
    novel's opening passage."""

    def test_a_reference_at_or_under_budget_is_returned_whole(self):
        ref = "Short reference, well under budget."
        assert engine_prompts._select_relevant_novel_passages(ref, budget_chars=1000) == ref

    def test_a_long_reference_with_no_relevance_signal_is_still_bounded(self):
        ref = "\n\n".join(f"Filler paragraph {i}. " * 20 for i in range(10))
        assert len(ref) > 500
        excerpt = engine_prompts._select_relevant_novel_passages(ref, budget_chars=500)
        assert len(excerpt) <= 500
        assert excerpt != ref
        # Falls back to the beginning, still bounded, not silently empty --
        # there's no relevance signal to rank by here.
        assert excerpt.startswith("Filler paragraph 0.")

    def test_selects_the_paragraph_matching_the_batchs_speaker_name(self):
        paragraphs = [
            "Opening chapter, sets the scene, no character named yet. " * 5,
            "A quiet scene about the weather, nothing relevant here. " * 5,
            "Xiaoling smiled and said something warm to her friend. " * 5,
        ]
        ref = "\n\n".join(paragraphs)
        excerpt = engine_prompts._select_relevant_novel_passages(
            ref, speaker_labels=["Xiaoling"], budget_chars=200)
        assert "Xiaoling" in excerpt
        assert "Opening chapter" not in excerpt

    def test_selects_the_paragraph_matching_a_glossary_terms_english_side(self):
        paragraphs = [
            "Opening chapter, sets the scene, nothing relevant here. " * 5,
            "Su Shan walked quietly through the garden at dusk. " * 5,
        ]
        ref = "\n\n".join(paragraphs)
        excerpt = engine_prompts._select_relevant_novel_passages(
            ref, batch_source_lines=["苏杉说了什么"],
            glossary_terms=[{"term_original": "苏杉", "term_translation": "Su Shan"}],
            budget_chars=200)
        assert "Su Shan" in excerpt
        assert "Opening chapter" not in excerpt

    def test_build_stable_prompt_records_which_excerpt_a_batch_actually_saw(self):
        """Step 50 item 3: the passages a batch actually saw are recorded
        on its own context, the same pattern as speaker_labels/line_ids,
        so a later "why did this line translate this way" check has
        something to look at."""
        context = {"drama_meta": {}, "novel_reference": "Short reference, under budget.",
                  "batch_source_lines": ["你好"]}
        _, novel_block = engine_prompts.build_stable_prompt(context)
        assert context["novel_reference_excerpt_used"] == "Short reference, under budget."
        assert "Short reference, under budget." in novel_block

    def test_translate_lines_with_engine_sends_a_bounded_relevant_excerpt_per_batch(self):
        engine = _claude_engine_with_fake_client()
        lines = [
            Line(idx=0, start=0, end=1, zh="第0句", id=100, speaker="A"),
            Line(idx=1, start=1, end=2, zh="第1句", id=101, speaker="B"),
        ]
        paragraphs = [
            "Opening chapter, sets the scene, no character named yet. " * 40,
            "Filler about the weather that nobody asked for. " * 40,
            "Xiaoling walked into the room and smiled warmly. " * 40,
            "Bo Wen sat quietly by the window, deep in thought. " * 40,
        ]
        ref = "\n\n".join(paragraphs)
        assert len(ref) > engine_prompts.NOVEL_REFERENCE_BUDGET_CHARS
        translate_pipeline.translate_lines_with_engine(
            lines, engine, {}, batch_size=1, novel_reference=ref,
            character_names={"A": "Xiaoling", "B": "Bo Wen"})
        calls = engine.client.messages.calls
        assert len(calls) == 2
        system_0 = "".join(b["text"] for b in calls[0]["system"])
        system_1 = "".join(b["text"] for b in calls[1]["system"])
        # Each batch gets the passage naming ITS OWN speaker, not the
        # novel's opening chapter every time, and not the whole file.
        assert "Xiaoling walked into the room" in system_0
        assert "Bo Wen sat quietly" not in system_0
        assert "Bo Wen sat quietly" in system_1
        assert "Xiaoling walked into the room" not in system_1
        assert len(system_0) < len(ref) and len(system_1) < len(ref)


class TestCacheUsageAccounting:
    def test_claude_usage_counts_cached_tokens_as_part_of_the_prompt(self):
        u = engine_shared.claude_usage(_FakeClaudeUsage(input_tokens=50, output_tokens=10,
                                             cache_read=900, cache_write=0))
        assert u == {"input_tokens": 950, "output_tokens": 10,
                     "cache_read_tokens": 900, "cache_write_tokens": 0}

    def test_gemini_usage_reads_cached_content_token_count(self):
        u = engine_shared.gemini_usage({"promptTokenCount": 1000, "candidatesTokenCount": 5,
                             "cachedContentTokenCount": 800})
        assert u["input_tokens"] == 1000 and u["cache_read_tokens"] == 800

    def test_cache_reads_cost_less_than_uncached_input(self):
        full = engine_pricing.estimate_cost("claude-sonnet-5", 1_000_000, 0)
        cached = engine_pricing.estimate_cost("claude-sonnet-5", 1_000_000, 0, cache_read_tokens=1_000_000)
        assert cached == pytest.approx(full * engine_pricing.CACHE_READ_PRICE_FACTOR)

    def test_cache_reads_use_the_models_own_discount(self):
        full = engine_pricing.estimate_cost("claude-sonnet-5-5", 1_000_000, 0)
        cached = engine_pricing.estimate_cost("claude-sonnet-5-5", 1_000_000, 0, cache_read_tokens=1_000_000)
        assert cached == pytest.approx(full * 0.05)

    def test_cache_writes_cost_more_than_uncached_input(self):
        full = engine_pricing.estimate_cost("claude-sonnet-5", 1_000_000, 0)
        written = engine_pricing.estimate_cost("claude-sonnet-5", 1_000_000, 0, cache_write_tokens=1_000_000)
        assert written == pytest.approx(full * engine_pricing.CACHE_WRITE_PRICE_FACTOR)

    def test_usage_cb_receives_cache_tokens_per_batch(self):
        engine = _claude_engine_with_fake_client(
            _FakeClaudeUsage(input_tokens=10, output_tokens=5, cache_read=90))
        seen = []
        translate_pipeline.translate_lines_with_engine(_six_lines(), engine, {}, batch_size=3,
                                       usage_cb=lambda *a: seen.append(a))
        assert seen == [(100, 5, 90, 0), (100, 5, 90, 0)]


# ---------------------------------------------------------------------------
# Step 9: spending caps
# ---------------------------------------------------------------------------

class _PricedEngine:
    """Every batch reports 1M input tokens on claude-sonnet-5 -- $2.00 a
    batch at PRICING_PER_MILLION_TOKENS, so cap arithmetic is exact."""
    name = "claude"
    supports_reference = True
    model = "claude-sonnet-5"

    def __init__(self):
        self.calls = 0
        self.last_usage = {}

    def translate_batch(self, zh_lines, context):
        self.calls += 1
        self.last_usage = {"input_tokens": 1_000_000, "output_tokens": 0}
        return [f"EN:{z}" for z in zh_lines]


class TestCostCap:
    def _lines(self, n=6):
        return [Line(idx=i, start=i, end=i + 1, zh=f"l{i}") for i in range(n)]

    def test_stops_before_the_batch_that_would_pass_the_cap_and_keeps_finished_lines(self):
        lines, engine, seen = self._lines(), _PricedEngine(), []
        _, errors = translate_pipeline.translate_lines_with_engine(
            lines, engine, {}, batch_size=2, cost_cap_usd=3.0, cap_cb=seen.append)
        assert engine.calls == 1
        assert [ln.en for ln in lines] == ["EN:l0", "EN:l1", "", "", "", ""]
        assert seen == [pytest.approx(2.0)]
        assert errors == []

    def test_the_first_batch_is_priced_from_the_estimate_before_it_is_sent(self):
        lines, engine, seen = self._lines(), _PricedEngine(), []
        # The bare instructions alone cost more than this, so even batch one does not fit.
        _, errors = translate_pipeline.translate_lines_with_engine(
            lines, engine, {}, batch_size=2, cost_cap_usd=0.0001, cap_cb=seen.append)
        assert engine.calls == 0 and seen == [0.0] and errors == []

    def test_the_first_batch_estimate_counts_the_titles_own_prompt(self):
        # Fits under the cap on the bare instructions; a long style guide sent with every
        # batch does not, so it must not be sent.
        bare, long_guide = _PricedEngine(), _PricedEngine()
        translate_pipeline.translate_lines_with_engine(self._lines(), bare, {}, batch_size=2, cost_cap_usd=0.05)
        _, errors = translate_pipeline.translate_lines_with_engine(
            self._lines(), long_guide, {}, batch_size=2, cost_cap_usd=0.05,
            style_guidelines="Keep every honorific as written. " * 20_000)
        assert bare.calls == 1 and long_guide.calls == 0 and errors == []

    def test_no_cap_runs_everything(self):
        lines, engine = self._lines(), _PricedEngine()
        translate_pipeline.translate_lines_with_engine(lines, engine, {}, batch_size=2)
        assert engine.calls == 3 and all(ln.en for ln in lines)

    def test_reaching_the_cap_on_the_last_batch_is_not_reported_as_a_stop(self):
        lines, engine, seen = self._lines(4), _PricedEngine(), []
        translate_pipeline.translate_lines_with_engine(lines, engine, {}, batch_size=2, cost_cap_usd=4.0,
                                       cap_cb=seen.append)
        assert engine.calls == 2 and all(ln.en for ln in lines)
        assert seen == []

    def test_a_free_tier_engine_never_hits_a_cap(self):
        lines, engine = self._lines(), _PricedEngine()
        engine.free_tier = True
        translate_pipeline.translate_lines_with_engine(lines, engine, {}, batch_size=2, cost_cap_usd=0.01)
        assert engine.calls == 3


class TestMonthCapReRead:
    def _lines(self, n=6):
        return [Line(idx=i, start=i, end=i + 1, zh=f"l{i}") for i in range(n)]

    def test_another_runs_spend_stops_this_run_before_its_next_batch(self):
        lines, engine, seen = self._lines(), _PricedEngine(), []
        # Spent by a second run once this one has sent its first batch.
        translate_pipeline.translate_lines_with_engine(
            lines, engine, {}, batch_size=2, month_cap_spent_cb=lambda: engine.calls >= 1,
            cap_cb=seen.append)
        assert engine.calls == 1
        assert [ln.en for ln in lines][:2] == ["EN:l0", "EN:l1"] and not lines[2].en

    def test_no_callback_runs_everything(self):
        lines, engine = self._lines(), _PricedEngine()
        translate_pipeline.translate_lines_with_engine(lines, engine, {}, batch_size=2)
        assert engine.calls == 3


class TestResolveCostCap:
    def test_no_caps_means_no_cap(self):
        assert engine_pricing.resolve_cost_cap(None, None, 5.0) == (None, None)
        assert engine_pricing.resolve_cost_cap(0, 0, 5.0) == (None, None)

    def test_job_cap_alone(self):
        assert engine_pricing.resolve_cost_cap(2.5, None, 100.0) == (2.5, None)

    def test_monthly_remaining_alone(self):
        assert engine_pricing.resolve_cost_cap(None, 10.0, 7.5) == (pytest.approx(2.5), None)

    def test_the_tighter_of_the_two_wins(self):
        assert engine_pricing.resolve_cost_cap(1.0, 10.0, 7.5)[0] == 1.0
        assert engine_pricing.resolve_cost_cap(5.0, 10.0, 7.5)[0] == pytest.approx(2.5)

    def test_monthly_cap_used_up_refuses(self):
        cap, refusal = engine_pricing.resolve_cost_cap(5.0, 10.0, 10.0)
        assert cap is None
        assert "already used up" in refusal


class TestEstimateTranslationCost:
    def test_free_tier_estimates_zero(self):
        engine = _PricedEngine()
        engine.free_tier = True
        assert engine_pricing.estimate_translation_cost(engine, ["你好"] * 100) == 0.0


class TestScheduledPrices:
    def _flash_cost(self, monkeypatch, day):
        import datetime

        class _Dt(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(day.year, day.month, day.day, tzinfo=tz)

        monkeypatch.setattr(engine_pricing.datetime, "datetime", _Dt)
        return engine_pricing.estimate_cost("gemini-flash-latest", 1_000_000, 1_000_000)

    def test_gemini_flash_price_doubles_on_jan_1_2027(self, monkeypatch):
        import datetime
        assert self._flash_cost(monkeypatch, datetime.date(2026, 12, 31)) == pytest.approx(4.50)
        assert self._flash_cost(monkeypatch, datetime.date(2027, 1, 1)) == pytest.approx(9.00)

    def test_haiku_5_5_long_prompts_cost_more(self):
        short = engine_pricing.estimate_cost("claude-haiku-5-5", 100_000, 0)
        long = engine_pricing.estimate_cost("claude-haiku-5-5", 100_001, 0)
        assert short == pytest.approx(0.01) and long > short * 4

    def test_price_tables_were_checked_recently(self):
        import datetime
        age = datetime.date.today() - engine_pricing.PRICES_VERIFIED_ON
        assert age.days <= 90, ("Re-check PRICING_PER_MILLION_TOKENS against the providers' pricing "
                                "pages, then update PRICES_VERIFIED_ON.")
