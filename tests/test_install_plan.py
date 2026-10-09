"""What an install would change and whether to run it now (install_plan.py).
pip's report is faked JSON, loaded files are faked modules, and Windows paths
go through ntpath; no real pip."""
import json
import ntpath
import os
import subprocess
from importlib import metadata
from pathlib import PurePosixPath
from types import ModuleType, SimpleNamespace

import pytest

import install_plan as plan_mod
import install_registry


def report(*pairs):
    return {"version": "1", "install": [{"metadata": {"name": n, "version": v}} for n, v in pairs]}


def build(rep, installed, windows=True, in_use=lambda name: False, **kw):
    return plan_mod.build_plan(kw.pop("keys", ["paddleocr"]), report=rep, installed=installed,
                               windows=windows, in_use=in_use, mins=kw.pop("mins", {}),
                               required=kw.pop("required", set()), **kw)


def test_parse_report_reads_names_and_versions():
    doc = report(("PaddleOCR", "3.2.0"), ("opencv_contrib_python", "4.10.0.84"))
    assert plan_mod.parse_report(doc) == [("paddleocr", "3.2.0"), ("opencv-contrib-python", "4.10.0.84")]
    assert plan_mod.parse_report({}) == [] and plan_mod.parse_report(None) == []
    assert plan_mod.parse_report({"install": [{"metadata": {"name": 1}}, {}]}) == []


def test_plan_lists_installs_and_changes_in_plain_words():
    p = build(report(("paddleocr", "3.2.0"), ("paddlepaddle", "3.0.0"), ("numpy", "2.3.5"),
                     ("requests", "2.33.0")),
              {"numpy": "2.5.3", "requests": "2.33.0"}, windows=False)
    assert p["summary"] == ["Install paddleocr 3.2.0, paddlepaddle 3.0.0.",
                            "Change numpy 2.5.3 -> 2.3.5 (downgrade)."]
    assert [c["name"] for c in p["changes"]] == ["paddleocr", "paddlepaddle", "numpy"]   # requests unchanged
    assert p["before"] == {"numpy": "2.5.3"}


def test_upgrade_is_not_called_a_downgrade():
    p = build(report(("numpy", "2.6.0")), {"numpy": "2.5.3"}, windows=False)
    assert p["changes"][0]["kind"] == "upgrade" and p["needs_confirm"] == []


def test_downgrading_something_the_app_requires_needs_extra_confirmation():
    p = build(report(("numpy", "2.3.5")), {"numpy": "2.5.3"}, windows=False, required={"numpy"})
    assert p["needs_confirm"] and "numpy 2.5.3 -> 2.3.5" in p["needs_confirm"][0]
    assert p["blocked"] == []


def test_downgrading_a_package_with_a_declared_minimum_needs_confirmation():
    p = build(report(("pillow", "11.0")), {"pillow": "12.3"}, windows=False, mins={"pillow": "12.3"})
    assert p["needs_confirm"]


def test_downgrading_something_the_app_does_not_need_is_listed_but_not_blocked():
    p = build(report(("pyyaml", "5.4")), {"pyyaml": "6.0"}, windows=False)
    assert p["needs_confirm"] == [] and p["changes"][0]["kind"] == "downgrade"


def test_a_second_opencv_flavour_is_refused_with_a_plain_message():
    p = build(report(("paddleocr", "3.2.0"), ("opencv-contrib-python", "4.10.0.84")),
              {"opencv-python": "4.9.0.80"}, windows=False)
    assert p["blocked"] and "two OpenCV packages" in p["blocked"][0]
    assert "opencv-contrib-python" in p["blocked"][0] and "opencv-python" in p["blocked"][0]


def test_upgrading_the_opencv_you_have_is_not_a_clash():
    p = build(report(("opencv-python", "4.11.0.86")), {"opencv-python": "4.9.0.80"}, windows=False)
    assert p["blocked"] == []


def test_two_flavours_the_owner_already_had_do_not_block_an_unrelated_install():
    p = build(report(("paddleocr", "3.2.0")),
              {"opencv-python": "4.9", "opencv-contrib-python": "4.9"}, windows=False)
    assert p["blocked"] == []


def test_fresh_opencv_install_is_not_a_clash():
    p = build(report(("opencv-python", "4.11.0.86")), {}, windows=False)
    assert p["blocked"] == [] and p["changes"][0]["kind"] == "install"


def test_loaded_package_that_pip_would_replace_waits_for_restart_on_windows():
    p = build(report(("numpy", "2.6.0"), ("paddleocr", "3.2.0")), {"numpy": "2.5.3"},
              in_use=lambda name: name == "numpy")
    assert p["mode"] == "restart" and p["loaded"] == ["numpy"]
    assert "Windows can't replace" in p["note"]


def test_nothing_loaded_installs_immediately_as_before():
    p = build(report(("numpy", "2.6.0"), ("paddleocr", "3.2.0")), {"numpy": "2.5.3"})
    assert p["mode"] == "now" and p["loaded"] == []


def test_a_new_package_needs_no_check_because_nothing_is_replaced():
    calls = []
    p = build(report(("paddleocr", "3.2.0")), {}, in_use=lambda n: calls.append(n) or True)
    assert p["mode"] == "now" and calls == []


def test_off_windows_files_can_be_replaced_so_the_fast_path_stays():
    p = build(report(("numpy", "2.6.0")), {"numpy": "2.5.3"}, windows=False, in_use=lambda n: True)
    assert p["mode"] == "now" and p["loaded"] == []


def test_a_running_job_means_restart_on_windows():
    p = build(report(("paddleocr", "3.2.0")), {}, jobs_running=True)
    assert p["mode"] == "restart" and "job is running" in p["note"]


def test_no_preview_means_restart_on_windows_and_now_elsewhere(monkeypatch):
    monkeypatch.setattr(plan_mod, "run_dry_run", lambda keys: (None, "pip could not work out the change (are you online?)."))
    win = plan_mod.build_plan(["paddleocr"], windows=True)
    assert win["available"] is False and win["mode"] == "restart" and "online" in win["note"]
    assert plan_mod.build_plan(["paddleocr"], windows=False)["mode"] == "now"


def test_pip_saying_the_packages_conflict_blocks_the_plan(monkeypatch):
    monkeypatch.setattr(plan_mod, "run_dry_run", lambda keys: (None, "conflict"))
    p = plan_mod.build_plan(["paddleocr"], windows=True)
    assert p["blocked"] and "can't be installed together" in p["blocked"][0]


def test_unknown_keys_and_cuda_torch_are_blocked_before_pip_runs(monkeypatch):
    monkeypatch.setattr(plan_mod, "run_dry_run", lambda keys: pytest.fail("pip must not run"))
    assert plan_mod.build_plan(["evil"])["blocked"] == ["Unknown or non-installable package."]
    monkeypatch.setattr(install_registry.shutil, "which", lambda n: "/x/nvidia-smi")
    assert "GPU PyTorch" in plan_mod.build_plan(["torch"])["blocked"][0]


# --- the dry run itself ---------------------------------------------------------

def fake_runner(doc=None, rc=0, text=""):
    seen = {}

    def run(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        out = argv[argv.index("--report") + 1]
        if doc is not None:
            with open(out, "w", encoding="utf-8") as f:
                json.dump(doc, f)
        return SimpleNamespace(returncode=rc, stdout=text, stderr="")
    run.seen = seen
    return run


def test_dry_run_runs_the_registry_argv_with_dry_run_and_report():
    run = fake_runner(report(("paddleocr", "3.2.0")))
    doc, why = plan_mod.run_dry_run(["paddleocr", "paddlepaddle"], runner=run)
    argv = run.seen["argv"]
    assert why is None and doc["install"]
    assert argv[1:4] == ["-m", "pip", "install"] and "--dry-run" in argv
    assert argv[argv.index("paddleocr"):argv.index("paddleocr") + 2] == ["paddleocr", "paddlepaddle"]
    assert run.seen["kw"]["timeout"] == plan_mod.PLAN_TIMEOUT_SECONDS
    assert not any(a.startswith("--index-url") for a in argv)
    assert not os.path.exists(argv[argv.index("--report") + 1])        # temp report removed


def test_dry_run_failure_reasons():
    assert plan_mod.run_dry_run(["paddleocr"], runner=fake_runner(rc=1, text="ResolutionImpossible"))[1] == "conflict"
    assert "online" in plan_mod.run_dry_run(["paddleocr"], runner=fake_runner(rc=1, text="no network"))[1]
    assert "cannot preview" in plan_mod.run_dry_run(["paddleocr"], runner=fake_runner())[1]

    def broken(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 1)
    assert "could not be run" in plan_mod.run_dry_run(["paddleocr"], runner=broken)[1]


# --- is it loaded? ----------------------------------------------------------------

SITE = "C:\\Python312\\Lib\\site-packages"


class FakeDist:
    def __init__(self, files):
        self.files = [PurePosixPath(f) for f in files]

    def locate_file(self, f):
        return ntpath.join(SITE, *PurePosixPath(f).parts)


def module(path):
    m = ModuleType("m")
    m.__file__ = path
    return m


def winnorm(p):
    return ntpath.normcase(ntpath.normpath(p))


def check(files, modules):
    return plan_mod.dist_in_use("opencv-python", modules=modules, norm=winnorm,
                                lookup=lambda n: FakeDist(files))


def test_loaded_compiled_extension_is_detected_through_its_path_case_insensitively():
    files = ["cv2/__init__.py", "cv2/cv2.pyd", "opencv_python-4.9.dist-info/METADATA"]
    mods = {"x": module("c:\\python312\\LIB\\site-packages\\cv2\\cv2.pyd")}
    assert check(files, mods) is True


def test_imported_top_level_of_a_compiled_dist_counts_even_if_the_pyd_is_not_listed():
    files = ["numpy/__init__.py", "numpy/_core/_multiarray_umath.pyd", "numpy.libs/openblas.dll"]
    assert check(files, {"numpy": module("c:\\elsewhere\\numpy\\__init__.py")}) is True


def test_a_dll_folder_marks_its_package_as_in_use_when_that_package_is_imported():
    files = ["onnxruntime/capi/onnxruntime.dll"]
    assert check(files, {"onnxruntime": module("x")}) is True


def test_compiled_dist_that_was_never_imported_is_free_to_replace():
    assert check(["cv2/cv2.pyd", "cv2/__init__.py"], {"os": module("c:\\py\\os.py")}) is False


def test_pure_python_dist_is_never_in_use():
    assert check(["pyyaml_x/__init__.py", "pyyaml_x/core.py"], {"pyyaml_x": module("a.py")}) is False


def test_a_dist_that_is_not_installed_is_not_in_use():
    def missing(name):
        raise metadata.PackageNotFoundError(name)
    assert plan_mod.dist_in_use("nope", modules={}, lookup=missing) is False


def test_real_loaded_module_is_found_on_this_machine():
    import sys
    # json is pure Python and stdlib: no dist, so not in use; a real dist with
    # compiled files that this test run imported (pydantic_core) is.
    assert plan_mod.dist_in_use("definitely-not-installed-xyz") is False
    try:
        import pydantic_core  # noqa: F401
    except ImportError:
        pytest.skip("pydantic_core not installed")
    assert "pydantic_core" in sys.modules
    assert plan_mod.dist_in_use("pydantic-core") is True
