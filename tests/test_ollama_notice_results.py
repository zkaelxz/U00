"""The Ollama notice reaches job results, the and the Jobs list."""
from services import jobs_service


def test_the_notice_is_in_the_job_result_projection_and_summary():
    notice = "Ollama still has a model loaded (a:1b), which may make transcription run out of GPU memory."
    result = {"line_count": 3, "ollama_notice": notice}
    outcome, message = jobs_service.derive_outcome("done", None, result)
    assert outcome == "partial" and notice in message
    assert "ollama_notice" in jobs_service.RESULT_ALLOWED_KEYS

