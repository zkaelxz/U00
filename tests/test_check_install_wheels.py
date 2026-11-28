"""scripts/check_install_wheels.py: an install pin needs a win_amd64 wheel for the shipped Python."""

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("packaging")

_spec = importlib.util.spec_from_file_location(
    "check_install_wheels", Path(__file__).resolve().parent.parent / "scripts" / "check_install_wheels.py")
ciw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ciw)


@pytest.mark.parametrize("name,fits", [
    ("pkg-1.0-cp313-cp313-win_amd64.whl", True),
    ("pkg-1.0-cp312-cp312-win_amd64.whl", False),
    ("pkg-1.0-cp313-cp313-manylinux_2_17_x86_64.whl", False),
    ("pkg-1.0-py3-none-any.whl", True),
    ("pkg-1.0-cp39-abi3-win_amd64.whl", True),
    ("pkg-1.0-cp314-abi3-win_amd64.whl", False),
    ("pkg-1.0.tar.gz", False),
])
def test_wheel_fit(name, fits):
    assert ciw.wheel_fits(name, "3.13") is fits


def test_a_pin_without_a_fitting_file_is_reported():
    files = {"a==1": ["a-1-cp312-cp312-win_amd64.whl"], "b==2": ["b-2-cp313-cp313-win_amd64.whl"]}
    assert ciw.check(["a==1", "b==2"], "3.13", fetch=files.__getitem__) == ["a==1"]


def test_the_app_pins_are_collected():
    pins = ciw.exact_pins("3.13")
    assert any(p.startswith("paddlepaddle==") for p in pins)
    assert any(p.startswith("onnxruntime-gpu==") for p in pins)
