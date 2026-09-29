"""Tests for services/workflow_service.py (UI-free). Copied from
tests/test_workspace_tab.py::TestWorkspaceStageIndex, which stays until the
Streamlit tab is deleted (Streamlit retirement M0a)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import Line
from services.workflow_service import compute_workspace_stage_index, stage_statuses_from_index



class TestWorkspaceStageIndex:
    """Regression coverage for Step 14 (Workspace shell rebuild): the
    project header's pipeline-progress stepper needs a real stage index
    computed from the drama's actual state, not a guess -- this is the
    function that computes it, and ui.workflow.stage_statuses_from_index
    turns that single index into a done/current/not-started list for
    each of the 7 stage tabs."""

    STAGES = ["Source", "Transcript", "Diarize", "Translate", "Review", "Dub", "Export"]

    def test_brand_new_drama_with_no_source_content_is_still_on_source(self, tmp_path):
        # A drama that's just been created (content_mode picked, nothing
        # uploaded yet) hasn't finished Source -- it shouldn't jump straight
        # to Transcript just because it also has no lines yet.
        idx = compute_workspace_stage_index({"content_mode": "audio_drama"}, None, str(tmp_path))
        assert idx == 0

    def test_source_uploaded_but_not_yet_transcribed_is_on_transcript(self, tmp_path):
        idx = compute_workspace_stage_index(
            {"content_mode": "audio_drama", "audio_filename": "audio.mp3"}, None, str(tmp_path))
        assert idx == 1

    def test_novel_narration_with_no_saved_novel_text_is_still_on_source(self, tmp_path):
        idx = compute_workspace_stage_index(
            {"content_mode": "novel_narration"}, None, str(tmp_path))
        assert idx == 0

    def test_novel_narration_with_saved_novel_text_is_on_transcript(self, tmp_path):
        (tmp_path / "novel_narration_source.txt").write_text("some text")
        idx = compute_workspace_stage_index(
            {"content_mode": "novel_narration"}, None, str(tmp_path))
        assert idx == 1

    def test_lines_with_no_speaker_yet_is_on_diarize(self, tmp_path):
        lines = [Line(idx=0, start=0, end=1, zh="你好", en="", speaker=None)]
        idx = compute_workspace_stage_index({"content_mode": "audio_drama"}, lines, str(tmp_path))
        assert idx == 2

    def test_novel_narration_has_no_diarize_stage(self, tmp_path):
        # No audio to diarize -- speaker attribution is the translation
        # LLM's job, not a separate stage, so an unset speaker shouldn't
        # hold the stepper at Diarize the way it does for audio content.
        lines = [Line(idx=0, start=0, end=1, zh="你好", en="", speaker=None)]
        idx = compute_workspace_stage_index({"content_mode": "novel_narration"}, lines, str(tmp_path))
        assert idx == 3

    def test_partway_translated_shows_transcribe_diarize_done_translate_current(self, tmp_path):
        # The manual check from Step 14's exit conditions: open a drama
        # with lines already translated partway through, and confirm the
        # stepper shows Transcribe/Diarize done, Translate in progress,
        # Review/Dub/Export not started.
        lines = [
            Line(idx=0, start=0, end=1, zh="你好", en="Hello", speaker="A"),
            Line(idx=1, start=1, end=2, zh="再见", en="", speaker="B"),
        ]
        idx = compute_workspace_stage_index({"content_mode": "audio_drama"}, lines, str(tmp_path))
        assert idx == 3

        statuses = stage_statuses_from_index(self.STAGES, idx)
        assert statuses == ["done", "done", "done", "current", "not_started",
                             "not_started", "not_started"]

    def test_fully_translated_not_yet_dubbed_or_exported_is_on_review(self, tmp_path):
        lines = [Line(idx=0, start=0, end=1, zh="你好", en="Hello", speaker="A")]
        idx = compute_workspace_stage_index({"content_mode": "audio_drama"}, lines, str(tmp_path))
        assert idx == 4

    def test_dub_track_on_disk_moves_to_export(self, tmp_path):
        (tmp_path / "dub_track.wav").write_bytes(b"")
        lines = [Line(idx=0, start=0, end=1, zh="你好", en="Hello", speaker="A")]
        idx = compute_workspace_stage_index({"content_mode": "audio_drama"}, lines, str(tmp_path))
        assert idx == 6

    def test_exported_status_is_fully_done(self, tmp_path):
        lines = [Line(idx=0, start=0, end=1, zh="你好", en="Hello", speaker="A")]
        idx = compute_workspace_stage_index(
            {"content_mode": "audio_drama", "status": "exported"}, lines, str(tmp_path))
        assert idx == 6
        assert stage_statuses_from_index(self.STAGES, idx) == \
            ["done", "done", "done", "done", "done", "done", "current"]

    def test_exported_with_no_persisted_speaker_still_shows_export_not_diarize(self, tmp_path):
        # Step 45: a real reported drama had fully translated lines and was
        # marked exported, but no line ever had a `speaker` value persisted
        # (diarization was skipped, or its result never saved) -- the old
        # check order treated "no speaker on any line" as unconditionally
        # meaning Diarize is current, even though translation/export had
        # clearly moved well past that. Export/dub now outrank Diarize.
        lines = [Line(idx=0, start=0, end=1, zh="你好", en="Hello", speaker=None),
                 Line(idx=1, start=1, end=2, zh="再见", en="Goodbye", speaker=None)]
        idx = compute_workspace_stage_index(
            {"content_mode": "audio_drama", "status": "exported"}, lines, str(tmp_path))
        assert idx == 6

    def test_fully_translated_with_no_persisted_speaker_shows_review_not_diarize(self, tmp_path):
        # Same missing-speaker shape as above, but not yet exported/dubbed --
        # translation being fully done should still take the stepper past
        # Diarize to Review, not leave it stuck reporting Diarize forever.
        lines = [Line(idx=0, start=0, end=1, zh="你好", en="Hello", speaker=None)]
        idx = compute_workspace_stage_index({"content_mode": "audio_drama"}, lines, str(tmp_path))
        assert idx == 4


def test_stage_statuses_from_index():
    stages = ["Source", "Translate", "Export"]
    assert stage_statuses_from_index(stages, 1) == ["done", "current", "not_started"]
    assert stage_statuses_from_index(stages, 0) == ["current", "not_started", "not_started"]
    assert stage_statuses_from_index(stages, None) == ["not_started"] * 3
