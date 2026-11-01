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
