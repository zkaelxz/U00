"""
Tests for services/source_service.py -- Migration Slice 19's Source-stage
config (read + a narrow, validated partial update). Audio/video upload
and transcript/novel-narration text are deliberately out of scope -- see
the module's own docstring for why.
"""
import os

import pytest

from services import source_service
from services.service_errors import InvalidInputError, NotFoundError, UnsupportedOperationError


class TestGetSourceConfig:
    def test_unknown_drama_raises_not_found(self, isolated_db):
        with pytest.raises(NotFoundError):
            source_service.get_source_config(999999)

    def test_fresh_drama_defaults(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        result = source_service.get_source_config(did)
        assert result == {
            "drama_id": did,
            "source_language": "zh",
            "chinese_script": "simplified",
            "content_mode": "audio_drama",
            "has_audio_pipeline": True,
            "audio_available": False,
            "has_video_source": False,
            "transcript_mode": "have_transcript",
            "transcript_mode_options": ["have_transcript", "whisper"],
            "has_raw_novel_context": False,
        }

    def test_novel_narration_has_no_audio_pipeline(self, isolated_db):
        did = isolated_db.create_drama(title_en="D", content_mode="novel_narration")
        result = source_service.get_source_config(did)
        assert result["has_audio_pipeline"] is False

    def test_video_source_adds_hardsub_ocr_option(self, isolated_db):
        did = isolated_db.create_drama(title_en="D", source_video_filename="x.mp4")
        result = source_service.get_source_config(did)
        assert result["has_video_source"] is True
        assert "hardsub_ocr" in result["transcript_mode_options"]

    def test_audio_available_reflects_a_real_file_on_disk(self, isolated_db):
        did = isolated_db.create_drama(title_en="D", audio_filename="a.wav")
        ddir = isolated_db.drama_dir(did)
        os.makedirs(ddir, exist_ok=True)
        assert source_service.get_source_config(did)["audio_available"] is False
        open(os.path.join(ddir, "a.wav"), "wb").close()
        assert source_service.get_source_config(did)["audio_available"] is True

    def test_raw_novel_context_presence(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        ddir = isolated_db.drama_dir(did)
        os.makedirs(ddir, exist_ok=True)
        assert source_service.get_source_config(did)["has_raw_novel_context"] is False
        with open(os.path.join(ddir, "raw_novel_context.txt"), "w") as f:
            f.write("some novel text")
        assert source_service.get_source_config(did)["has_raw_novel_context"] is True


class TestUpdateSourceConfig:
    def test_unknown_drama_raises_not_found(self, isolated_db):
        with pytest.raises(NotFoundError):
            source_service.update_source_config(999999, source_language="ja")

    def test_updates_source_language(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        result = source_service.update_source_config(did, source_language="ja")
        assert result["source_language"] == "ja"

    def test_unknown_source_language_is_invalid_input(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        with pytest.raises(InvalidInputError):
            source_service.update_source_config(did, source_language="en")

    def test_unknown_content_mode_is_invalid_input(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        with pytest.raises(InvalidInputError):
            source_service.update_source_config(did, content_mode="not_a_mode")

    def test_streamer_vod_syncs_media_type(self, isolated_db):
        did = isolated_db.create_drama(title_en="D", media_type="asmr")
        source_service.update_source_config(did, content_mode="streamer_vod")
        drama = isolated_db.get_drama(did)
        assert drama["media_type"] == "streamer_vod"

    def test_switching_away_from_streamer_vod_does_not_revert_media_type(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        source_service.update_source_config(did, content_mode="streamer_vod")
        source_service.update_source_config(did, content_mode="audio_drama")
        drama = isolated_db.get_drama(did)
        # One-directional sync, matching workspace_tab.py's own existing behavior.
        assert drama["media_type"] == "streamer_vod"

    def test_audio_drama_content_mode_does_not_touch_media_type(self, isolated_db):
        did = isolated_db.create_drama(title_en="D", media_type="asmr")
        source_service.update_source_config(did, content_mode="audio_drama")
        drama = isolated_db.get_drama(did)
        assert drama["media_type"] == "asmr"

    def test_hardsub_ocr_without_video_is_unsupported_operation(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        with pytest.raises(UnsupportedOperationError):
            source_service.update_source_config(did, transcript_mode="hardsub_ocr")

    def test_hardsub_ocr_with_video_succeeds(self, isolated_db):
        did = isolated_db.create_drama(title_en="D", source_video_filename="x.mp4")
        result = source_service.update_source_config(did, transcript_mode="hardsub_ocr")
        assert result["transcript_mode"] == "hardsub_ocr"

    def test_unknown_chinese_script_is_invalid_input(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        with pytest.raises(InvalidInputError):
            source_service.update_source_config(did, chinese_script="pinyin")

    def test_no_fields_passed_is_a_no_op_returning_current_config(self, isolated_db):
        did = isolated_db.create_drama(title_en="D", source_language="ja")
        result = source_service.update_source_config(did)
        assert result["source_language"] == "ja"

    def test_only_passed_fields_are_changed(self, isolated_db):
        did = isolated_db.create_drama(title_en="D", source_language="ja")
        source_service.update_source_config(did, chinese_script="traditional")
        drama = isolated_db.get_drama(did)
        assert drama["source_language"] == "ja"
