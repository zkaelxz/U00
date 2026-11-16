"""RapidOCR installs without its dependencies so OpenCV's 'cv2' folder is never touched."""

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
