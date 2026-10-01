"""The source-language and media-extension constants have one home each."""
import typing

import asr_benchmark
import core
import epub_io
import line_tools
import forced_align
import resegment
import story_context
import translate_engines
import translation_guide
from api import benchmark_schemas
from services import (reader_service, source_service, benchmark_lab_service, discover_catalog_service, drama_service,
                      live_service, media_export_service, media_upload_service,
                      transcribe_service, voice_clone_service)


def test_language_constants_cover_the_three_languages():
    assert core.SOURCE_LANGUAGES == ("zh", "ja", "ko")
    assert core.LANGUAGE_NAMES == {"zh": "Chinese", "ja": "Japanese", "ko": "Korean"}
    assert set(core.LANGUAGE_NAMES) == set(core.SOURCE_LANGUAGES)


def test_language_copies_are_the_shared_objects():
    assert forced_align.LANGUAGE_NAMES is core.LANGUAGE_NAMES
    assert translate_engines.LANGUAGE_NAMES is core.LANGUAGE_NAMES
    for mod in (resegment, story_context, translation_guide):
        assert mod.LANGUAGE_NAMES is core.LANGUAGE_NAMES
    assert drama_service.SOURCE_LANGUAGES is core.SOURCE_LANGUAGES
    assert live_service.SOURCE_LANGUAGES is core.SOURCE_LANGUAGES
    assert discover_catalog_service.SOURCE_LANGUAGES is core.SOURCE_LANGUAGES
    assert benchmark_lab_service.SOURCE_LANGUAGES is core.SOURCE_LANGUAGES
    assert transcribe_service.SOURCE_LANGUAGES is core.SOURCE_LANGUAGES
    assert not hasattr(transcribe_service, "_SOURCE_LANGUAGES")
    assert not hasattr(drama_service, "_SOURCE_LANGUAGES")
    assert not hasattr(discover_catalog_service, "LANGUAGES")


def test_benchmark_schema_literal_matches_source_languages():
    field = benchmark_schemas.BenchmarkCaseCreate.model_fields["source_language"]
    assert typing.get_args(field.annotation) == core.SOURCE_LANGUAGES


def test_media_extension_copies_are_gone():
    assert media_export_service.VIDEO_EXTENSIONS is media_upload_service.VIDEO_EXTENSIONS
    assert voice_clone_service.AUDIO_EXTENSIONS is media_upload_service.AUDIO_EXTENSIONS
    assert not hasattr(media_export_service, "_VIDEO_EXTS")
    assert not hasattr(voice_clone_service, "CLIP_EXTENSIONS")


def test_remaining_language_copies_use_core():
    assert line_tools.LANGUAGE_NAMES is core.LANGUAGE_NAMES
    assert reader_service.core_module.LANGUAGE_NAMES is core.LANGUAGE_NAMES
    assert source_service.SOURCE_LANGUAGES is core.SOURCE_LANGUAGES
    assert not hasattr(source_service, "_SOURCE_LANGUAGES")
    assert epub_io.SOURCE_LANGUAGES is core.SOURCE_LANGUAGES
    assert asr_benchmark.SOURCE_LANGUAGES is core.SOURCE_LANGUAGES
