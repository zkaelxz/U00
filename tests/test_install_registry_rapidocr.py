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
