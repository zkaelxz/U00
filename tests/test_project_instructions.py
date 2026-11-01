"""
Tests for Step 12e: persisted per-drama / per-series project instructions
that reach the translation prompt, and the Draft / Standard / Release
starting tiers.
"""

from engine_backends import prompts, engine_registry, pricing


def _prompt(drama_meta, style_note=""):
    return prompts.build_llm_instructions(style_note, drama_meta)


class TestInstructionsInPrompt:
    def test_drama_instructions_appear_alongside_the_global_style_note(self):
        prompt = _prompt({"project_instructions": "Keep the narrator distant."},
                         style_note="Short lines.")
        assert "Keep the narrator distant." in prompt
        assert "- Additional style notes: Short lines." in prompt
        # Two separate entries, not one merged into the other.
        assert "Short lines.\n- Project instructions" in prompt

    def test_series_instructions_come_first_then_the_dramas_own(self):
        prompt = _prompt({"series_instructions": "SERIES RULE",
                          "project_instructions": "DRAMA RULE"})
        assert prompt.index("SERIES RULE") < prompt.index("DRAMA RULE")

    def test_multi_line_instructions_are_kept_whole(self):
        text = "Line one.\nLine two.\n\nLine four."
        assert text in _prompt({"project_instructions": text})

    def test_no_instructions_means_no_block(self):
        for meta in ({}, {"project_instructions": "   ", "series_instructions": None}):
            assert "Project instructions" not in _prompt(meta)
        assert prompts.build_project_instructions_block({}) == ""

    def test_instructions_stay_before_the_output_format_rule(self):
        prompt = _prompt({"project_instructions": "DRAMA RULE"})
        assert prompt.index("DRAMA RULE") < prompt.index("Return ONLY a JSON object")


class TestInstructionsPersistAndInherit:
    def test_drama_instructions_round_trip_and_reach_the_prompt(self, isolated_db):
        did = isolated_db.create_drama(title_en="A")
        isolated_db.update_drama(did, project_instructions="Formal register throughout.")
        drama = isolated_db.get_drama(did)
        assert drama["project_instructions"] == "Formal register throughout."
        assert "Formal register throughout." in _prompt(drama)

    def test_second_drama_in_the_series_inherits_series_instructions(self, isolated_db):
        sid = isolated_db.get_or_create_series("Series X")
        first = isolated_db.create_drama(title_en="Ep 1", series_id=sid)
        second = isolated_db.create_drama(title_en="Ep 2", series_id=sid)
        other = isolated_db.create_drama(title_en="Unrelated")
        isolated_db.update_series_instructions(sid, "Keep 师姐 as Shijie.")
        isolated_db.update_drama(first, project_instructions="Only for episode 1.")

        ep2 = isolated_db.get_drama(second)
        assert ep2["series_instructions"] == "Keep 师姐 as Shijie."
        assert not ep2.get("project_instructions")
        prompt = _prompt(ep2)
        assert "Keep 师姐 as Shijie." in prompt and "Only for episode 1." not in prompt

        assert "Keep 师姐 as Shijie." not in _prompt(isolated_db.get_drama(other))

        # list_dramas (the CLI's source of drama_meta) carries it too.
        by_id = {d["id"]: d for d in isolated_db.list_dramas()}
        assert by_id[second]["series_instructions"] == "Keep 师姐 as Shijie."
        assert by_id[other]["series_instructions"] is None

    def test_list_dramas_filters_still_work(self, isolated_db):
        isolated_db.create_drama(title_en="Needle", status="aligned")
        isolated_db.create_drama(title_en="Hay", status="translated")
        assert [d["title_en"] for d in isolated_db.list_dramas(search="Needle")] == ["Needle"]
        assert [d["title_en"] for d in isolated_db.list_dramas(status="translated")] == ["Hay"]

    def test_init_db_is_idempotent_with_the_new_columns(self, isolated_db):
        isolated_db.init_db()
        isolated_db.init_db()


class TestWorkflowTiers:
    EXPECTED = {
        "draft": ("deepseek", None, False, False),
        "standard": ("claude", "claude-sonnet-5-5", False, False),
        "release": ("claude", "claude-opus-5-5", True, True),
    }

    def test_tier_definitions(self):
        assert set(engine_registry.WORKFLOW_TIERS) == set(self.EXPECTED)
        for name, (engine, model, reflect, auto_qc) in self.EXPECTED.items():
            t = engine_registry.WORKFLOW_TIERS[name]
            assert (t["translation_engine"], t["engine_model"], t["reflect"], t["auto_qc"]) == (
                engine, model, reflect, auto_qc)
            assert t["translation_engine"] in engine_registry.ENGINES
            assert model is None or model in pricing.CLAUDE_MODELS




class TestTermConventions:
    TEXT = "Keep it formal.\n\nTerm conventions:\n- sects: translate\n- names: pinyin"

    def test_split_separates_prose_from_conventions(self):
        from services.glossary_common import split_term_conventions
        assert split_term_conventions(self.TEXT) == ("Keep it formal.", "- sects: translate\n- names: pinyin")
        assert split_term_conventions("Plain.") == ("Plain.", "")
        assert split_term_conventions("Term conventions:\n- a") == ("", "- a")

    def test_conventions_reach_the_translate_prompt(self):
        prompt = _prompt({"project_instructions": self.TEXT})
        assert "- sects: translate" in prompt and "- names: pinyin" in prompt

    def test_conventions_reach_the_glossary_extraction_prompt(self, monkeypatch):
        import translation_guide as tguide
        prompts = []
        monkeypatch.setattr(tguide, "call_llm_json",
                            lambda engine, prompt, **kw: prompts.append(prompt) or "[]")

        class Engine:
            supports_reference = True

        tguide.extract_glossary_from_novel("林" * 100, Engine(), conventions="- sects: translate")
        tguide.extract_glossary_from_novel("林" * 100, Engine())
        assert "- sects: translate" in prompts[0]
        assert "conventions for terms" not in prompts[1]

    def test_started_novel_run_passes_the_saved_conventions(self, isolated_db, monkeypatch):
        from services import glossary_extract_service as ges
        seen = {}
        monkeypatch.setattr(ges, "_start_extraction_job",
                            lambda job_id, target, *a, **kw: seen.update(kw) or True)
        monkeypatch.setattr(ges, "_read_drama_file", lambda *a, **kw: "原文")
        monkeypatch.setattr(ges, "_glossary_engine", lambda drama, name: ("claude", object()))
        monkeypatch.setattr("services.translate_run_service.refuse_when_cap_spent", lambda *a: None)
        sid = isolated_db.create_series("S")
        did = isolated_db.create_drama(title_zh="书", series_id=sid, media_type="novel",
                                       project_instructions=self.TEXT)
        ges.start_novel_glossary_run(did)
        assert seen["conventions"] == "- sects: translate\n- names: pinyin"
