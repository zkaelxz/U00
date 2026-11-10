"""Tests for tools/live_vram_watch.py: the summary of GPU and Ollama samples."""
from tools import live_vram_watch as watch


def test_summary_reports_peak_and_spilled_models():
    samples = [{"gpu": [(5000, 12288)], "ollama": [("a:4b", 1.0)]},
               {"gpu": [(11900, 12288)], "ollama": [("a:4b", 1.0), ("b:8b", 0.6)]}]
    assert watch.summarize(samples) == {"samples": 2, "peak_used_mib": 11900, "total_mib": 12288,
                                        "spilled_models": ["b:8b"]}


def test_summary_without_nvidia_smi_or_ollama():
    result = watch.summarize([{"gpu": [], "ollama": []}])
    assert result["peak_used_mib"] is None and result["spilled_models"] == []


def test_remote_ollama_url_is_refused():
    import pytest
    with pytest.raises(SystemExit):
        watch.main(["--ollama-url", "http://example.com:11434", "--minutes", "0"])
