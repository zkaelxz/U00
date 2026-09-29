"""
Tests for Step 12e: persisted per-drama / per-series project instructions
that reach the translation prompt, and the Draft / Standard / Release
starting tiers.
"""

import translate_engines as te


def _prompt(drama_meta, style_note=""):
    return te.build_llm_instructions(style_note, drama_meta)


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
        assert te.build_project_instructions_block({}) == ""

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
        "standard": ("claude", "claude-sonnet-5", False, False),
        "release": ("claude", "claude-opus-4-8", True, True),
    }

    def test_tier_definitions(self):
        assert set(te.WORKFLOW_TIERS) == set(self.EXPECTED)
        for name, (engine, model, reflect, auto_qc) in self.EXPECTED.items():
            t = te.WORKFLOW_TIERS[name]
            assert (t["translation_engine"], t["engine_model"], t["reflect"], t["auto_qc"]) == (
                engine, model, reflect, auto_qc)
            assert t["translation_engine"] in te.ENGINES
            assert model is None or model in te.CLAUDE_MODELS


