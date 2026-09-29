"""
Test-only launcher for the end-to-end check (frontend/e2e/library.spec.ts):
starts the real FastAPI app against a throwaway library seeded with a few
known dramas, so the browser test exercises React -> FastAPI -> the real
service layer and db.py, without ever touching a person's actual library.

Usage (Playwright's webServer runs this for you):
    python frontend/e2e/serve_seeded_api.py [port]
"""

import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import db  # noqa: E402


def seed(library_dir: str):
    db.configure_library_dir(library_dir)
    db.create_drama(title_zh="魔道祖师", title_en="Grandmaster of Demonic Cultivation",
                    author="墨香铜臭", author_romanized="Mo Xiang Tong Xiu",
                    status="translated", media_type="audio_drama", source_language="zh",
                    custom_tags="Favorite, wuxia", summary="Seeded for the e2e test.")
    db.create_drama(title_zh="天官赐福", title_en="Heaven Official's Blessing",
                    status="aligned", media_type="novel", source_language="zh",
                    custom_tags="Plan to Translate")
    db.create_drama(title_zh="시그널", title_en="Signal", status="not started",
                    media_type="video_drama", source_language="ko")


E2E_STUB_OUTPUT = ["stubbed in e2e"]
E2E_STUB_TOKEN = "e2e-stub-token"


def install_e2e_stubs(setattr_=setattr):
    """Replace every action here that reaches outside the throwaway library:
    pip install/upgrade, the library reset, and the extension bridge's
    on/off and token. An e2e mock that leaks a request (a held route
    Chromium lets through when the page closes) then hits a stub, never a
    real pip run or the real extension token. `setattr_` lets a test pass
    monkeypatch.setattr so the stubs are undone afterwards."""
    from services import diagnostics_gaps_service as diag
    from services import extension_service as ext
    from services.service_errors import ConflictError

    def refuse_pip(name, confirm=False):
        raise ConflictError("Installing is disabled on the e2e server.")

    def refuse_reset(confirm=False, confirm_text=None):
        raise ConflictError("Resetting is disabled on the e2e server.")

    setattr_(diag, "install_dependency", refuse_pip)
    setattr_(diag, "upgrade_dependency", refuse_pip)
    # Second layer: nothing that reaches the command runner starts a process.
    setattr_(diag, "_run_commands", lambda cmds: {"ok": False, "output_tail": list(E2E_STUB_OUTPUT)})
    setattr_(diag, "reset_library", refuse_reset)
    setattr_(ext, "set_enabled", lambda enabled, start_now=True: {
        "enabled": bool(enabled), "running": False, "restart_needed": False})
    setattr_(ext, "reveal_token", lambda confirm=False: {"token": E2E_STUB_TOKEN})


def main():
    import uvicorn
    from api.api_config import ApiSettings
    from api.server import create_app

    install_e2e_stubs()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8611
    # A fixed, gitignored folder wiped at every start, rather than a temp
    # dir cleaned up on exit: Playwright may kill this process outright,
    # and a `finally` never runs then.
    library_dir = os.path.join(ROOT, "frontend", "test-results", "e2e-library")
    shutil.rmtree(library_dir, ignore_errors=True)
    os.makedirs(library_dir)
    seed(library_dir)
    uvicorn.run(create_app(ApiSettings(port=port)), host="127.0.0.1", port=port,
                log_level="warning")


if __name__ == "__main__":
    main()
