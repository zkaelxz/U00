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
        assert "qwen_asr" not in text


def test_repo_ids_are_the_transformers_native_checkpoints():
    assert qwen3_native.asr_repo_id("1.7B") == "Qwen/Qwen3-ASR-1.7B-hf"
    assert qwen3_native.asr_repo_id("0.6B") == "Qwen/Qwen3-ASR-0.6B-hf"
    assert qwen3_native.ALIGNER_REPO == "Qwen/Qwen3-ForcedAligner-0.6B-hf"


def test_chatterbox_pin_is_a_known_downgrade_of_transformers(monkeypatch):
    monkeypatch.setattr(diagnostics, "get_installed_version",
                        lambda name: "5.19.0" if name == "transformers" else None)
    warning = diagnostics.install_downgrade_warning("chatterbox-tts")
    assert warning and "5.2.0" in warning and "Qwen3-ASR" in warning
    assert diagnostics.install_downgrade_warning("omnivoice") is None
