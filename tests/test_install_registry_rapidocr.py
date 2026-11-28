"""RapidOCR installs without its dependencies so OpenCV's 'cv2' folder is never touched."""

from pathlib import Path

import install_registry


def test_rapidocr_install_skips_dependencies_and_adds_its_requirements():
    keys = sorted(install_registry.RAPIDOCR_KEYS)
    argv = install_registry.install_argv(keys)
    assert argv[argv.index("install") + 1] == "--no-deps"
    assert "rapidocr" in argv and "shapely" in argv
    assert not any("opencv" in a for a in argv)


def test_other_installs_keep_their_dependencies():
    assert "--no-deps" not in install_registry.install_argv(["manga_ocr"])


def test_rapidocr_cannot_share_an_install_with_other_packages():
    assert install_registry.unqueueable_reason(["rapidocr", "shapely", "manga_ocr"])
    assert install_registry.unqueueable_reason(sorted(install_registry.RAPIDOCR_KEYS)) is None


def test_a_single_package_install_of_rapidocr_skips_its_dependencies(monkeypatch):
    from services import diagnostics_packages_service as svc
    monkeypatch.setattr(svc.pending_install, "snapshot", lambda: {})
    ((cmd, _),) = svc._install_commands("rapidocr")
    assert "--no-deps" in cmd and cmd[-1] != "--no-deps"
    ((cmd, _),) = svc._install_commands("pytesseract")
    assert "--no-deps" not in cmd


def test_a_queued_rapidocr_install_is_accepted_by_the_apply_step():
    import pending_install
    argv = install_registry.install_argv(sorted(install_registry.RAPIDOCR_KEYS))
    assert pending_install._argv_accepted(argv)


def test_upgrading_rapidocr_skips_its_dependencies_like_installing_it(monkeypatch):
    from services import diagnostics_packages_service as svc
    seen = {}
    monkeypatch.setattr(svc, "_run_pip", lambda name, confirm, cmds, job_id=None: seen.update(
        {"rapidocr": cmds("rapidocr")[0][0], "pytesseract": cmds("pytesseract")[0][0]}))
    monkeypatch.setattr(svc, "_clear_update_cache", lambda: None)
    svc.upgrade_dependency("rapidocr", ["rapidocr==3.10.0"])
    assert "--no-deps" in seen["rapidocr"] and seen["rapidocr"][-1] != "--no-deps"
    assert "--no-deps" not in seen["pytesseract"]


def test_rapidocr_requirements_cover_its_metadata():
    # rapidocr 3.10.0 requires these (besides numpy, Pillow and requests, which the app has,
    # and opencv-python-headless, which is the one we skip) plus an inference engine.
    assert {"onnxruntime", "shapely", "pyclipper", "omegaconf", "colorlog", "six", "tqdm",
            "PyYAML"} <= install_registry.RAPIDOCR_KEYS


def test_paddleocr_installs_without_its_dependencies_so_numpy_and_opencv_stay():
    # PaddleX pins numpy<2.4, PyYAML==6.0.2 and opencv-contrib-python==4.10.0.84; pip would
    # downgrade the versions Baihe needs, and Diagnostics refused that install.
    argv = install_registry.install_argv(["paddleocr", "paddlepaddle"])
    assert argv[argv.index("install") + 1] == "--no-deps"
    assert "paddleocr==3.7.0" in argv and "paddlepaddle==3.3.1" in argv and "paddlex==3.7.2" in argv
    assert not any(a.split("==")[0].lower() in ("numpy", "pyyaml") or "opencv" in a for a in argv)


def test_paddleocr_needs_no_opencv_swap_and_can_be_queued():
    import pending_install
    assert install_registry.cv2_swap_names(["paddleocr"], {"opencv-python": "5.0.0.93"}) == []
    argv = install_registry.install_argv(["paddleocr", "paddlepaddle"])
    assert pending_install._argv_accepted(argv)
    assert install_registry.unqueueable_reason(["paddleocr", "paddlepaddle"]) is None
    assert install_registry.unqueueable_reason(["paddleocr", "manga_ocr"])
    assert install_registry.unqueueable_reason(["paddleocr", "rapidocr"]) is None


def test_the_one_package_install_route_also_installs_paddles_requirements(monkeypatch):
    # The Install button of a single package must give the same command as the queued install:
    # without paddlex and the rest, `import paddleocr` fails after --no-deps.
    from services import diagnostics_packages_service as svc
    monkeypatch.setattr(svc.pending_install, "snapshot", lambda: {})
    ((cmd, _),) = svc._install_commands("paddleocr")
    assert "--no-deps" in cmd and "paddleocr==3.7.0" in cmd and "paddlex==3.7.2" in cmd
    assert "modelscope-hub" in cmd and "yarl" in cmd
    ((cmd, _),) = svc._install_commands("pytesseract")
    assert cmd[-1].endswith("constraints.txt") or "pytesseract" in cmd


def test_paddles_install_names_all_pass_the_apply_steps_name_check():
    import pending_install
    assert pending_install._argv_accepted(install_registry.install_argv(["paddleocr", "paddlepaddle"]))


def test_onnxruntime_gpu_replaces_the_cpu_build_and_back():
    import pending_install
    both = {"onnxruntime": "1.32.0"}
    assert install_registry.swap_names(["onnxruntime-gpu"], both) == ["onnxruntime"]
    assert install_registry.swap_names(["onnxruntime-gpu"], {}) == []
    assert install_registry.swap_names(["onnxruntime"], {"onnxruntime-gpu": "1.19.2"}) == ["onnxruntime-gpu"]
    for name in ("onnxruntime", "onnxruntime-gpu"):
        assert pending_install._uninstall_steps_accepted([install_registry.uninstall_argv(name)])
    argv = install_registry.install_argv(["onnxruntime-gpu"])
    assert install_registry.ORT_PINS["onnxruntime-gpu"] in argv and pending_install._argv_accepted(argv)
    try:
        install_registry.uninstall_argv("numpy")
    except ValueError:
        pass
    else:
        raise AssertionError("only the swappable packages may be uninstalled")


def test_onnxruntime_gpu_needs_an_nvidia_card_and_cannot_join_the_cpu_build(monkeypatch):
    monkeypatch.setattr(install_registry.shutil, "which", lambda name: None)
    assert "NVIDIA" in install_registry.unqueueable_reason(["onnxruntime-gpu"])
    monkeypatch.setattr(install_registry.shutil, "which", lambda name: "nvidia-smi")
    assert install_registry.unqueueable_reason(["onnxruntime-gpu"]) is None
    assert "replace each other" in install_registry.unqueueable_reason(["onnxruntime-gpu", "onnxruntime"])


def test_the_one_package_route_previews_removes_then_installs_for_onnxruntime_gpu(monkeypatch):
    from services import diagnostics_packages_service as svc
    monkeypatch.setattr(svc.pending_install, "snapshot", lambda: {"onnxruntime": "1.32.0"})
    cmds = [c for c, _ in svc._install_commands("onnxruntime-gpu")]
    assert "--dry-run" in cmds[0] and cmds[1][-3:] == ["uninstall", "-y", "onnxruntime"]
    assert install_registry.ORT_PINS["onnxruntime-gpu"] in cmds[2]


def test_onnxruntime_gpu_is_found_by_its_distribution_not_an_import_name(monkeypatch):
    import diagnostics
    slot = diagnostics.OPTIONAL_DEPENDENCIES["onnxruntime-gpu"][0]
    monkeypatch.setattr(diagnostics, "get_installed_version", lambda name: "1.19.2" if name == "onnxruntime-gpu" else None)
    assert diagnostics.check_dependency(slot) is True
    monkeypatch.setattr(diagnostics, "get_installed_version", lambda name: None)
    assert diagnostics.check_dependency(slot) is False


def test_the_gpu_text_reader_pin_has_a_wheel_for_the_python_and_fits_the_constraints():
    from packaging.requirements import Requirement
    root = Path(__file__).resolve().parents[1]
    cap = next(line.split("#")[0].strip() for line in (root / "constraints.txt").read_text().splitlines()
               if line.startswith("onnxruntime-gpu"))
    limit = Requirement(cap).specifier
    for v, first_wheel in (((3, 12), (1, 19, 2)), ((3, 13), (1, 20, 0))):
        version = install_registry.ort_gpu_pin(v).split("==")[1]
        assert version in limit
        assert tuple(map(int, version.split("."))) >= first_wheel
    assert install_registry.ort_gpu_pin((3, 14)) == install_registry.ort_gpu_pin((3, 13))


def test_the_gpu_text_reader_pin_accepts_a_dotted_version_string():
    assert install_registry.ort_gpu_pin("3.13") == install_registry.ort_gpu_pin((3, 13))
    assert install_registry.ort_gpu_pin("3.13.17") == install_registry.ort_gpu_pin((3, 13))
    assert install_registry.ort_gpu_pin("3.12") == install_registry.ORT_GPU_BY_PYTHON[(3, 12)]
