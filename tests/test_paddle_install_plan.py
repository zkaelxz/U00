"""How the install plan treats what a PaddleOCR install drags along: numpy and
PyYAML below the app's pins, and PaddleX's exact opencv-contrib pin. pip's
report is faked; the app's real requirements files decide what counts as
"needed by Baihe"."""
import pytest

import install_plan as plan_mod
import install_registry
import diagnostics

PADDLE_REPORT = {"install": [{"metadata": {"name": n, "version": v}} for n, v in (
    ("paddleocr", "3.7.0"), ("paddlepaddle", "3.3.1"), ("opencv-contrib-python", "4.10.0.84"),
    ("numpy", "2.3.5"), ("PyYAML", "6.0.2"))]}
APP_ENV = {"numpy": "2.5.3", "pyyaml": "6.0.3"}


def _plan(installed):
    return plan_mod.build_plan(["paddleocr", "paddlepaddle"], report=PADDLE_REPORT,
                               installed=installed, windows=False)


def _opencv_blocks(p):
    return [b for b in p["blocked"] if "OpenCV" in b]


def test_a_numpy_downgrade_below_the_app_minimum_is_blocked():
    p = _plan({**APP_ENV, "opencv-contrib-python": "4.10.0.84"})
    assert any("numpy 2.5.3 -> 2.3.5" in b and "older than the" in b for b in p["blocked"])


def test_a_numpy_downgrade_that_stays_above_the_minimum_needs_confirmation(monkeypatch):
    monkeypatch.setattr(plan_mod.dependency_updates, "required_min_versions", lambda: {"numpy": "2.0"})
    p = _plan({**APP_ENV, "opencv-contrib-python": "4.10.0.84"})
    assert p["blocked"] == []
    assert any("numpy 2.5.3 -> 2.3.5" in risk for risk in p["needs_confirm"])


def test_the_pyyaml_downgrade_is_listed_without_a_confirmation():
    p = _plan({**APP_ENV, "opencv-contrib-python": "4.10.0.84"})
    assert "pyyaml" not in plan_mod.app_required_names()
    assert any("pyyaml 6.0.3 -> 6.0.2 (downgrade)" in line for line in p["summary"])
    assert not any("pyyaml" in risk for risk in p["needs_confirm"])


@pytest.mark.parametrize("flavour", ["opencv-python", "opencv-python-headless"])
def test_a_plain_opencv_is_swapped_out_not_blocked(flavour):
    p = _plan({**APP_ENV, flavour: "4.10.0.84"})
    assert _opencv_blocks(p) == [] and p["swap"] == [flavour]
    assert p["summary"][0].startswith(f"Remove {flavour} first")
    assert p["before"][flavour] == "4.10.0.84"          # so a failed install puts it back


def test_a_contrib_environment_gets_no_swap():
    p = _plan({**APP_ENV, "opencv-contrib-python": "4.9.0.80"})
    assert p["swap"] == [] and _opencv_blocks(p) == []


def test_an_environment_without_opencv_gets_no_swap():
    assert _plan(APP_ENV)["swap"] == []


def test_the_swap_counts_a_loaded_cv2_as_in_use_on_windows():
    p = plan_mod.build_plan(["paddleocr", "paddlepaddle"], report=PADDLE_REPORT, windows=True,
                            installed={**APP_ENV, "opencv-python": "4.10.0.84"},
                            in_use=lambda name: name == "opencv-python")
    assert p["loaded"] == ["opencv-python"] and p["mode"] == "restart"


def test_contrib_over_contrib_is_not_a_clash():
    assert _opencv_blocks(_plan({**APP_ENV, "opencv-contrib-python": "4.9.0.80"})) == []


def test_the_registry_offers_both_paddle_packages():
    assert install_registry.unqueueable_reason(["paddleocr", "paddlepaddle"]) is None
    assert {"paddleocr", "paddlepaddle"} <= set(diagnostics.OPTIONAL_DEPENDENCIES)


def test_a_plan_without_a_preview_still_discloses_the_removal(monkeypatch):
    monkeypatch.setattr(plan_mod, "run_dry_run", lambda keys, specs=None: (None, "offline"))
    p = plan_mod.build_plan(["paddleocr"], windows=False,
                            installed={**APP_ENV, "opencv-python": "4.10.0.84"})
    assert p["swap"] == ["opencv-python"] and p["summary"][0].startswith("Remove opencv-python first")
    assert p["available"] is False
