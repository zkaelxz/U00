"""
tests/test_qwen_asr_version.py -- Step 76: requirements-optional.txt pinned
`qwen-asr>=0.1`, but `pip index versions qwen-asr` shows real published
releases only go up to 0.0.6 (0.0.1-0.0.6) -- nothing at or above 0.1 has
ever shipped, so that line could never resolve. Confirmed compatible: in a
real throwaway venv, `asr_backend.load_qwen3_asr()`/`Qwen3ASRModel.transcribe()`
and `forced_align.load_qwen3_aligner()`/`Qwen3ForcedAligner.align()` both ran
end to end against the real 0.0.6 package (real HF download, real CPU
inference) with this app's code unchanged -- their call signatures match
exactly what 0.0.6 actually exposes.

Following this project's own testing rule (mocked, no live network calls in
the suite -- see Step 75's own drift-prevention tests in
test_static_analysis.py, which are pure static checks), this doesn't call
PyPI live. Instead it pins the real, dated fact from that `pip index
versions` check as a constant, the same way Step 47/61's
KNOWN_UPGRADE_LIMITATIONS records a reproduced finding rather than
re-deriving it every run. If a real 0.0.7+ (or 0.1+) release ships later and
someone wants to bump the floor to it, this constant needs bumping too --
deliberately, not silently.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Confirmed via `pip index versions qwen-asr` on 2026-09-27: the real
# published releases are 0.0.1 through 0.0.6. Nothing higher exists.
QWEN_ASR_LAST_CONFIRMED_PUBLISHED_VERSION = (0, 0, 6)


def _qwen_asr_requirement():
    path = os.path.join(ROOT, "requirements-optional.txt")
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"\s*qwen-asr\s*(>=|==)\s*([0-9.]+)", line)
            if m:
                return m.group(1), tuple(int(p) for p in m.group(2).split("."))
    raise AssertionError("no qwen-asr line found in requirements-optional.txt")


def test_qwen_asr_floor_is_a_version_that_actually_exists_on_pypi():
    op, version = _qwen_asr_requirement()
    assert version <= QWEN_ASR_LAST_CONFIRMED_PUBLISHED_VERSION, (
        f"requirements-optional.txt pins qwen-asr{op}{'.'.join(map(str, version))}, but the "
        f"last confirmed real PyPI release is {'.'.join(map(str, QWEN_ASR_LAST_CONFIRMED_PUBLISHED_VERSION))} "
        f"-- re-run `pip index versions qwen-asr` before raising this floor, the exact bug "
        f"this test exists to catch (the old >=0.1 floor could never install)."
    )


def test_qwen_asr_floor_is_not_the_old_unresolvable_0_1():
    """The specific regression: >=0.1 (or higher) can never resolve since
    no such release has ever shipped."""
    _op, version = _qwen_asr_requirement()
    assert version < (0, 1), (
        "qwen-asr's floor is back at or above 0.1 -- that version has never been "
        "published (pip index versions qwen-asr tops out at 0.0.6) and this line "
        "can never install."
    )


def test_asr_backend_and_forced_align_call_signatures_match_the_real_package():
    """Confirms, offline, that asr_backend.py/forced_align.py call the real
    qwen_asr 0.0.6 API by name and keyword -- these were verified with a
    real end-to-end run (real HF download + CPU inference) in a throwaway
    venv, not just by reading the source; this pins that shape so a future
    edit to either module that drifts from the real package's signature
    fails a test instead of only failing at runtime for someone who
    actually installs qwen-asr."""
    for path, must_contain in [
        ("asr_backend.py", [
            "from qwen_asr import Qwen3ASRModel",
            "Qwen3ASRModel.from_pretrained(",
            "model.transcribe(audio=", "language=language_name",
        ]),
        ("forced_align.py", [
            "from qwen_asr import Qwen3ForcedAligner",
            "Qwen3ForcedAligner.from_pretrained(",
            "model.align(audio=", "text=concatenated", "language=language_name",
        ]),
    ]:
        with open(os.path.join(ROOT, path), encoding="utf-8") as f:
            text = f.read()
        for needle in must_contain:
            assert needle in text, f"{path} no longer calls {needle!r}"
