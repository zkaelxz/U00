"""The whole-episode pre-read brief and the series note that reach a subtitle translate run."""
import pytest

import background_jobs
from core import Line
from engine_backends import llm_tasks, prompts as engine_prompts
from services import settings_service, workspace_job_service as wjs


class _Engine:
    supports_reference = True
    client = object()
    model = "m"


def _series(db, summaries):
    sid = db.get_or_create_series("Saga")
    ids = [db.create_drama(title_en=f"Ep {n}", series_id=sid, episode_number=n, episode_summary=s)
           for n, s in summaries]
    return sid, ids


def _drama_with_lines(db, **fields):
    did = db.create_drama(title_en="D", **fields)
    db.save_lines(did, [Line(idx=0, start=0, end=2, zh="你好", speaker="S0"),
                        Line(idx=1, start=2, end=4, zh="再见", speaker="S1")])
    return did, db.load_line_objects(did)


class TestEarlierEpisodeSummaries:
    def test_skips_the_previous_episode_and_every_later_one(self, isolated_db):
        _, ids = _series(isolated_db, [(1, "One."), (2, "Two."), (3, "Three."), (4, "Four."), (5, "Five.")])
        assert isolated_db.earlier_episode_summaries(ids[3]) == [(1, "One."), (2, "Two.")]
        assert isolated_db.earlier_episode_summaries(ids[0]) == []
        assert isolated_db.earlier_episode_summaries(ids[1]) == []

    def test_a_previous_episode_without_a_summary_is_still_not_repeated(self, isolated_db):
        _, ids = _series(isolated_db, [(1, "One."), (2, ""), (3, "Three.")])
        assert isolated_db.earlier_episode_summaries(ids[2]) == [(1, "One.")]

    def test_other_series_and_unnumbered_dramas_contribute_nothing(self, isolated_db):
        _series(isolated_db, [(1, "One."), (2, "Two.")])
        other = isolated_db.get_or_create_series("Other")
        isolated_db.create_drama(title_en="O1", series_id=other, episode_number=1,
                                 episode_summary="Other one.")
        loose = isolated_db.create_drama(title_en="Loose", series_id=other)
        assert isolated_db.earlier_episode_summaries(loose) == []
        assert isolated_db.earlier_episode_summaries(isolated_db.create_drama(title_en="Solo")) == []


class TestPromptBlock:
    def test_brief_and_series_note_reach_the_instructions(self):
        text = engine_prompts.build_llm_instructions(
            "", {"episode_brief": "Lin is the boss.", "series_note": "Episode 1: Lin arrives."})
        assert "Lin is the boss." in text and "Episode 1: Lin arrives." in text

    def test_nothing_set_adds_nothing(self):
        assert engine_prompts.build_episode_brief_block({"episode_brief": " ", "series_note": ""}) == ""

    def test_an_oversized_series_note_keeps_the_newest_episodes(self):
        note = "Episode 1: OLDEST\n" + "x" * 3000 + "\nEpisode 9: NEWEST"
        block = engine_prompts.build_episode_brief_block({"series_note": note})
        assert "NEWEST" in block and "OLDEST" not in block


class TestGenerateEpisodeBrief:
    def test_sends_the_whole_episode_with_speaker_names_and_returns_the_brief(self, monkeypatch):
        sent = {}

        def fake(engine, prompt, **kw):
            sent["prompt"] = prompt
            return '{"brief": "  Lin is the boss.  "}'
        monkeypatch.setattr(llm_tasks, "call_llm_json", fake)
        lines = [Line(idx=0, start=0, end=1, zh="你好", speaker="S0"),
                 Line(idx=1, start=1, end=2, zh="再见", speaker="S1"),
                 Line(idx=2, start=2, end=3, zh="(门声)", sfx=True)]
        brief = llm_tasks.generate_episode_brief(lines, _Engine(), {"source_language": "zh"},
                                                 {"S0": "Lin (she/her)"})
        assert brief == "Lin is the boss."
        assert "[Lin (she/her)] 你好" in sent["prompt"] and "再见" in sent["prompt"]
        assert "门声" not in sent["prompt"]

    @pytest.mark.parametrize("reply", [None, "", "not json", '{"other": 1}', '{"brief": 3}'])
    def test_an_unusable_answer_gives_no_brief(self, monkeypatch, reply):
        monkeypatch.setattr(llm_tasks, "call_llm_json", lambda *a, **k: reply)
        assert llm_tasks.generate_episode_brief(
            [Line(idx=0, start=0, end=1, zh="你好")], _Engine()) == ""

    def test_an_engine_that_cannot_follow_a_prompt_is_not_called(self, monkeypatch):
        monkeypatch.setattr(llm_tasks, "call_llm_json",
                            lambda *a, **k: pytest.fail("called"))
        assert llm_tasks.generate_episode_brief([Line(idx=0, start=0, end=1, zh="你好")], object()) == ""

    def test_the_brief_is_cut_to_what_every_batch_can_carry(self, monkeypatch):
        monkeypatch.setattr(llm_tasks, "call_llm_json",
                            lambda *a, **k: '{"brief": "' + "a" * 5000 + '"}')
        brief = llm_tasks.generate_episode_brief([Line(idx=0, start=0, end=1, zh="你好")], _Engine())
        assert len(brief) == llm_tasks.EPISODE_BRIEF_MAX_CHARS


@pytest.fixture
def job_id():
    from jobs import job_table
    jid = "translate_brief_test"
    job_table._jobs[jid] = {"status": "running", "progress": 0.0, "message": "",
                            "error": None, "cancel_requested": False, "result": None}
    yield jid
    job_table._jobs.pop(jid, None)


def _context(db, did, lines, jid, **kw):
    return wjs._with_episode_context(jid, did, lines, _Engine(), "claude",
                                     db.get_drama(did), {}, kw.get("target_ids"))


class TestEpisodeContext:
    def test_writes_stores_and_carries_the_brief(self, isolated_db, job_id, monkeypatch):
        calls = []
        monkeypatch.setattr(llm_tasks, "generate_episode_brief",
                            lambda *a, **k: calls.append(1) or "Lin is the boss.")
        did, lines = _drama_with_lines(isolated_db)
        meta = _context(isolated_db, did, lines, job_id)
        assert meta["episode_brief"] == "Lin is the boss."
        assert isolated_db.get_drama(did)["episode_brief"] == "Lin is the boss."
        # A second run reads the saved (possibly edited) brief and makes no call.
        meta = _context(isolated_db, did, lines, job_id)
        assert meta["episode_brief"] == "Lin is the boss." and len(calls) == 1

    def test_an_edited_brief_is_used_as_written(self, isolated_db, job_id, monkeypatch):
        monkeypatch.setattr(llm_tasks, "generate_episode_brief",
                            lambda *a, **k: pytest.fail("called"))
        did, lines = _drama_with_lines(isolated_db, episode_brief="My own brief.")
        assert _context(isolated_db, did, lines, job_id)["episode_brief"] == "My own brief."

    def test_the_off_switch_and_a_partial_run_make_no_call(self, isolated_db, job_id, monkeypatch):
        monkeypatch.setattr(llm_tasks, "generate_episode_brief",
                            lambda *a, **k: pytest.fail("called"))
        did, lines = _drama_with_lines(isolated_db)
        assert not _context(isolated_db, did, lines, job_id, target_ids={1}).get("episode_brief")
        settings_service.set_settings({"episode_brief": False})
        assert not _context(isolated_db, did, lines, job_id).get("episode_brief")

    def test_a_failed_call_never_stops_the_run(self, isolated_db, job_id, monkeypatch):
        def fail(*a, **k):
            raise RuntimeError("boom")
        monkeypatch.setattr(llm_tasks, "generate_episode_brief", fail)
        did, lines = _drama_with_lines(isolated_db)
        assert not _context(isolated_db, did, lines, job_id).get("episode_brief")
        assert not isolated_db.get_drama(did)["episode_brief"]

    def test_a_cancel_ends_the_run(self, isolated_db, job_id, monkeypatch):
        def cancel(*a, **k):
            raise background_jobs.JobCancelled(job_id)
        monkeypatch.setattr(llm_tasks, "generate_episode_brief", cancel)
        did, lines = _drama_with_lines(isolated_db)
        with pytest.raises(background_jobs.JobCancelled):
            _context(isolated_db, did, lines, job_id)

    def test_the_series_note_holds_earlier_episodes_only(self, isolated_db, job_id, monkeypatch):
        monkeypatch.setattr(llm_tasks, "generate_episode_brief", lambda *a, **k: "")
        _, ids = _series(isolated_db, [(1, "One."), (2, "Two."), (3, "Three."), (4, "Four.")])
        lines = [Line(idx=0, start=0, end=1, zh="你好"), Line(idx=1, start=1, end=2, zh="再见")]
        meta = _context(isolated_db, ids[2], lines, job_id)
        assert meta["series_note"] == "Episode 1: One."
