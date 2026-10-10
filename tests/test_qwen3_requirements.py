"""
tests/test_qwen3_requirements.py -- the Qwen3 models run on transformers' own
classes (qwen3_native), so the requirement that matters is transformers'
version. The floor is 5.15 because 5.13 and 5.14 ship the classes but lack the
`prompt=` argument and the "language <NAME><asr_text>" prefill that
Qwen/Qwen3-ASR-*-hf were trained with (checked against the released wheels).
These tests are static: no PyPI or Hub call.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import importlib.metadata

import diagnostics
import qwen3_native

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _requirements_lines():
    with open(os.path.join(ROOT, "requirements-optional.txt"), encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip() and not line.lstrip().startswith("#")]


def _floor(name):
    for line in _requirements_lines():
        m = re.match(rf"{re.escape(name)}\s*>=\s*([0-9.]+)", line, re.IGNORECASE)
        if m:
            return tuple(int(p) for p in m.group(1).split("."))
    raise AssertionError(f"no {name}>= line in requirements-optional.txt")


def test_requirements_floor_matches_the_version_the_code_checks():
    assert _floor("transformers")[:2] == qwen3_native.MIN_TRANSFORMERS[:2]


def test_japanese_and_korean_alignment_packages_are_required_lines():
    assert _floor("nagisa") and _floor("soynlp")
    assert set(qwen3_native.ALIGNER_LANGUAGE_PACKAGES.values()) == {"nagisa", "soynlp"}


def test_the_qwen_asr_package_is_gone_from_the_requirements_and_diagnostics():
    assert not any(line.lower().startswith("qwen-asr") for line in _requirements_lines())
    assert "qwen-asr" not in diagnostics.OPTIONAL_DEPENDENCIES
    assert "qwen-asr" not in diagnostics.APPROX_DOWNLOAD_MB
    assert not hasattr(diagnostics, "QWEN_ASR_FALLBACK_DEPS")


def test_the_modules_call_the_native_classes_by_name():
    """A drift guard in the spirit of the old signature pin: a refactor that
    stops using the native loaders fails here rather than only at runtime."""
    for path, needles in [
        ("asr_backend.py", ["qwen3_native.NativeQwen3ASR.from_pretrained(",
                            "model.transcribe(audio=", "language=language_name"]),
        ("forced_align.py", ["qwen3_native.NativeQwen3Aligner.from_pretrained(",
                             "model.align(audio=", "text=concatenated",
                             "language=language_name"]),
    ]:
        with open(os.path.join(ROOT, path), encoding="utf-8") as f:
            text = f.read()
        for needle in needles:
            assert needle in text, f"{path} no longer calls {needle!r}"
        # memory_headroom keys its Qwen3-ASR load check by this label.
        assert "qwen_asr" not in text.replace('before_load("qwen_asr"', "")


def test_repo_ids_are_the_transformers_native_checkpoints():
    assert qwen3_native.asr_repo_id("1.7B") == "Qwen/Qwen3-ASR-1.7B-hf"
    assert qwen3_native.asr_repo_id("0.6B") == "Qwen/Qwen3-ASR-0.6B-hf"
    assert qwen3_native.ALIGNER_REPO == "Qwen/Qwen3-ForcedAligner-0.6B-hf"


# ---------------------------------------------------------------------------
# Start-time checks, the old qwen-asr package, and the version parser
# ---------------------------------------------------------------------------

import importlib.util
import types

import pytest

from services import qwen3_requirements_service as reqs
from services.service_errors import DependencyUnavailableError


def _fake_installed(monkeypatch, missing=(), transformers="5.19.0", qwen_asr=None):
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name, *a: None if name in missing
                        else (object() if name in ("torch", "soundfile", "nagisa", "soynlp") else real(name, *a)))
    monkeypatch.setattr(qwen3_native, "installed_transformers_version", lambda: transformers)
    monkeypatch.setattr(qwen3_native, "installed_qwen_asr_version", lambda: qwen_asr)


@pytest.mark.parametrize("language,package", [("ja", "nagisa"), ("ko", "soynlp")])
def test_start_check_names_the_missing_tokeniser_package(monkeypatch, language, package):
    _fake_installed(monkeypatch, missing=(package,))
    with pytest.raises(DependencyUnavailableError) as err:
        reqs.require_qwen3_packages("Qwen3 forced alignment", language)
    assert package in err.value.message and "Diagnostics" in err.value.message
    reqs.require_qwen3_packages("Qwen3 forced alignment", "zh")
    reqs.require_qwen3_packages("Qwen3-ASR")          # no aligner, no tokeniser needed


def test_a_japanese_long_run_is_refused_at_start_without_nagisa(monkeypatch):
    from services import transcribe_service
    _fake_installed(monkeypatch, missing=("nagisa",))
    monkeypatch.setattr(transcribe_service, "_require_vad_packages", lambda: None)
    with pytest.raises(DependencyUnavailableError, match="nagisa"):
        transcribe_service._check_run_choices("whisper", "qwen3_asr_long", "whisper_diff", "ja")
    transcribe_service._check_run_choices("whisper", "qwen3_asr", "whisper_diff", "ja")
    transcribe_service._check_run_choices("whisper", "qwen3_asr_long", "whisper_diff", "zh")


@pytest.mark.parametrize("version,too_old", [
    ("5.14.1", True), ("5.15.0rc1", True), ("5.15.0.dev0", True),
    ("5.15.0", False), ("5.19.2", False), ("6.0.0", False)])
def test_prereleases_of_the_floor_are_too_old(monkeypatch, version, too_old):
    _fake_installed(monkeypatch, transformers=version)
    assert (qwen3_native.transformers_problem() is not None) is too_old


def test_old_transformers_with_qwen_asr_installed_says_how_to_get_out(monkeypatch):
    _fake_installed(monkeypatch, transformers="4.57.6", qwen_asr="0.0.6")
    problem = qwen3_native.transformers_problem("Qwen3-ASR")
    assert "pip uninstall qwen-asr" in problem and "holds it back" in problem
    with pytest.raises(DependencyUnavailableError, match="pip uninstall qwen-asr"):
        reqs.require_qwen3_packages("Qwen3-ASR")
    _fake_installed(monkeypatch, transformers="4.57.6", qwen_asr=None)
    assert "qwen-asr" not in qwen3_native.transformers_problem("Qwen3-ASR")


@pytest.mark.parametrize("qwen_asr,transformers,warns", [
    ("0.0.6", "4.57.6", True), ("0.0.6", "5.15.0", False),
    (None, "4.57.6", False), ("0.0.6", None, False)])
def test_startup_warns_about_qwen_asr_while_it_holds_transformers_back(
        monkeypatch, qwen_asr, transformers, warns):
    installed = {"qwen-asr": qwen_asr, "transformers": transformers}
    monkeypatch.setattr(diagnostics, "get_installed_version", lambda name: installed.get(name))
    message = diagnostics._warn_qwen_asr_package()
    assert bool(message) is warns
    if warns:
        assert "pip uninstall qwen-asr" in message and "Diagnostics" in message


def _fake_dist(name, requires):
    return types.SimpleNamespace(metadata={"Name": name}, requires=requires)


def test_diagnostics_does_not_hold_transformers_back_for_qwen_asrs_pin(monkeypatch):
    monkeypatch.setattr(importlib.metadata, "distributions", lambda: [
        _fake_dist("qwen-asr", ["transformers==4.57.6"]),
        _fake_dist("other", ["transformers<6"])])
    required_by = diagnostics.installed_requirements_on()
    assert [r for r, _ in required_by["transformers"]] == ["other"]
    result = diagnostics.classify_update("transformers", "4.57.6", ["4.57.6", "5.15.0"],
                                         {}, required_by)
    assert result["status"] == "update" and result["target"] == "5.15.0"


def test_diagnostics_row_shows_a_too_old_transformers_as_not_ready(monkeypatch):
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "4.57.6")
    row = next(r for r in diagnostics.get_model_engine_versions() if r["name"] == "Qwen3-ASR")
    assert "4.57.6" in row["version"] and "needs 5.15 or newer" in row["version"]
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "5.19.0")
    row = next(r for r in diagnostics.get_model_engine_versions() if r["name"] == "Qwen3-ASR")
    assert row["version"] == "5.19.0"


def test_missing_soundfile_is_named_before_any_model_loads(monkeypatch):
    _fake_installed(monkeypatch, missing=("soundfile",))
    with pytest.raises(DependencyUnavailableError, match="soundfile"):
        reqs.require_qwen3_packages("Qwen3-ASR")
