"""The Ollama notice reaches job results, the Jobs list and the CLI."""
import types

from services import jobs_service


def test_the_notice_is_in_the_job_result_projection_and_summary():
    notice = "Ollama still has a model loaded (a:1b), which may make transcription run out of GPU memory."
    result = {"line_count": 3, "ollama_notice": notice}
    outcome, message = jobs_service.derive_outcome("done", None, result)
    assert outcome == "partial" and notice in message
    assert "ollama_notice" in jobs_service.RESULT_ALLOWED_KEYS


def test_cmd_transcribe_prints_the_notice(monkeypatch, capsys):
    import cli
    from services import transcribe_service
    monkeypatch.setattr(transcribe_service, "start_transcribe_run", lambda *a, **k: {"job_id": "j"})
    monkeypatch.setattr(cli, "_wait_for_job", lambda *a, **k: (
        "partial", "m", {"line_count": 1, "ollama_notice": "Ollama still has a model loaded (a:1b)."}))
    monkeypatch.setattr(cli, "_read_transcript_option", lambda args: None)
    args = types.SimpleNamespace(
        id=1, whisper_size=None, asr_backend=None, beam_size=None, min_silence_ms=None, min_pause=None,
        vad_threshold=None, sensitivity=None, separation_backend=None, separate_vocals=None,
        language=None, chinese_script=None, diarize=False, num_speakers=None, min_speakers=None,
        max_speakers=None, initial_prompt=None, extra_names=None, vocab_hint=None)
    cli.cmd_transcribe(args)
    assert "WARNING: Ollama still has a model loaded (a:1b)." in capsys.readouterr().out
