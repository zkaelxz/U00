# Every top-level .py file expected to exist for the
# app to run. Kept as an explicit list (not auto-discovered) so a
# missing file shows up as "missing" rather than just not being checked.
EXPECTED_TOP_LEVEL_FILES = [
    "core.py", "db.py", "translate_engines.py",
    "diarize.py", "dub.py", "dub_narration.py", "video_export.py", "ocr.py", "segment.py",
    "dictionary.py", "reader.py", "scanlate.py", "metadata_lookup.py",
    "known_sites.py", "title_library.py", "vocab_export.py",
    "qa.py", "bulk_import.py", "epub_io.py", "cli.py", "diagnostics.py",
    "run_tests.py", "translation_guide.py", "glossary_io.py",
    "story_context.py", "storage.py", "universe_wiki.py", "background_jobs.py",
    "adaptive_style.py", "line_tools.py", "debug_view.py", "emotion.py", "en_cleanup.py", "page_fetch.py",
    "page_server.py", "device_tokens.py",
    "forced_align.py", "asr_backend.py", "asr_benchmark.py", "video_download.py",
    # This list had drifted -- these were all real,
    # hard-imported modules missing from it, which meant the missing-file
    # health check below could no longer actually catch one of them going
    # missing.
    "applog.py", "audio_preprocess.py", "auto_qc.py", "benchmark.py",
    "bulk_translate.py", "check_setup.py", "hardsub_ocr.py", "live_translate.py", "live_fetch.py",
    "navigator.py", "portable.py", "raw_transcript.py", "resegment.py",
    "sensevoice_tags.py", "sensitivity_preset.py", "subtitle_formats.py", "voice_id.py", "word_align.py",
    "translation_memory.py", "action_tiers.py", "media_inspect.py",
    "expected_files.py", "vad_segments.py", "mixed_language.py", "ollama_unload.py", "process_guard.py",   # the installed server's Job Object (python -m api imports it)
]
