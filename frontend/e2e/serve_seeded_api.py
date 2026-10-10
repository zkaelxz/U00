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


def install_e2e_stubs(setattr_=setattr, environ=None):
    """Replace every action here that reaches outside the throwaway library:
    pip install/upgrade, the library reset, the extension bridge's on/off
    and token, and every engine key / HF token. An e2e mock that leaks a
    request (a held route Chromium lets through when the page closes) then
    hits a stub, never a real pip run, the real extension token or a paid
    engine on the user's own key. `setattr_` lets a test pass
    monkeypatch.setattr so the stubs are undone afterwards; `environ`
    (default os.environ) is the environment the key variables are removed
    from."""
    from services import diagnostics_gaps_service as diag
    from services import diagnostics_installs_service as installs
    from services import extension_service as ext
    from services import settings_service
    from lib.errors import ConflictError

    def refuse_pip(name, confirm=False, target=None, job_id=None):
        raise ConflictError("Installing is disabled on the e2e server.")

    def refuse_torch_setup(variant=None, confirm=False, job_id=None):
        raise ConflictError("Installing is disabled on the e2e server.")

    def refuse_network(*a, **k):
        # The PyPI update check and the CUDA check (a torch subprocess).
        raise ConflictError("Disabled on the e2e server.")

    def refuse_reset(confirm=False, confirm_text=None):
        raise ConflictError("Resetting is disabled on the e2e server.")

    setattr_(diag, "install_dependency", refuse_pip)
    setattr_(diag, "upgrade_dependency", refuse_pip)
    setattr_(diag, "setup_gpu_torch", refuse_torch_setup)
    # The install routes start a job; refuse before one exists.
    setattr_(installs, "start_dependency_install", refuse_pip)
    setattr_(installs, "start_gpu_torch_setup", refuse_torch_setup)
    setattr_(diag, "check_package_updates", refuse_network)
    setattr_(diag, "check_gpu_torch", refuse_network)
    # Second layer: nothing that reaches the command runner (or the torch
    # verify subprocess) starts a process.
    setattr_(diag, "_run_commands", lambda cmds, torch_pins=None, sox_watch=None, job_id=None: {
        "ok": False, "output_tail": list(E2E_STUB_OUTPUT)})
    setattr_(diag, "verify_torch", lambda blocking=True, cancel=None: {"error": "stubbed in e2e"})
    setattr_(diag, "reset_library", refuse_reset)
    # The Transcribe button is disabled while faster-whisper is missing; specs that
    # press it need the config to say it is installed. transcription-missing mocks
    # the config to cover the missing case.
    import diagnostics
    real_check = diagnostics.check_dependency
    setattr_(diagnostics, "check_dependency",
             lambda name, *a, **k: True if name == "faster_whisper" else real_check(name, *a, **k))
    setattr_(ext, "set_enabled", lambda enabled, start_now=True: {
        "enabled": bool(enabled), "running": False, "restart_needed": False})
    setattr_(ext, "reveal_token", lambda confirm=False: {"token": E2E_STUB_TOKEN})
    # Keys: every server-side read goes through settings_service (the .env
    # parse and resolve_key, looked up as a module attribute everywhere), and
    # core/scanlate/huggingface_hub also read HF_TOKEN-style variables from
    # the process environment directly -- so blank all three.
    setattr_(settings_service, "read_env_file", lambda env_path=None: {})
    setattr_(settings_service, "resolve_key", lambda settings_key, env_path=None: None)
    environ = os.environ if environ is None else environ
    for key, names in settings_service.ENV_NAMES.items():
        if key in settings_service.KEY_WRITE_ENGINES:
            for name in names:
                environ.pop(name, None)
    environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"  # no cached `huggingface-cli login` token


# Test-only job hooks (frontend/e2e/running-job.spec.ts). The Diagnostics
# cancel flow and the Library "delete refused while a job runs" flow need a
# job that is genuinely running in this server process: a job_records row
# alone has no owner to read the cancel flag, so a fresh one stays "running"
# after Cancel until it goes stale (jobs_service.cancel_job). Starting a real,
# cooperative background_jobs job here gives the real path: it mirrors itself
# to job_records, heartbeats (so no stale sweep closes it), counts for
# drama_service.job_running_for_drama and stops as "cancelled" when
# POST /api/jobs/{id}/cancel sets its flag. A spec starts one for a drama it
# created itself and removes it afterwards, so the shared seeded library
# (and every spec that counts dramas or expects an empty job list) is
# unchanged. Mounted only by this launcher, never by the real app.
E2E_JOB_ID = r"^[a-z_]+_[0-9]+$"
E2E_HOLD_MAX_SECONDS = 300.0


def _hold_until_cancelled(job_id: str):
    import time

    import background_jobs
    deadline = time.monotonic() + E2E_HOLD_MAX_SECONDS
    background_jobs.update_progress(job_id, 0.1, "Held by the e2e test")
    while time.monotonic() < deadline:
        if background_jobs.is_cancel_requested(job_id):
            raise background_jobs.JobCancelled()
        time.sleep(0.1)


def add_e2e_job_routes(app):
    import background_jobs
    from fastapi import Body
    from lib.errors import ConflictError, InvalidInputError

    def _check(job_id: str):
        import re
        if not isinstance(job_id, str) or not re.match(E2E_JOB_ID, job_id):
            raise InvalidInputError("job_id must look like prefix_123.")

    def hold(job_id: str = Body(...), description: str = Body(...)):
        _check(job_id)
        if not background_jobs.start_job(job_id, _hold_until_cancelled, job_id,
                                         description=description):
            raise ConflictError("That job is already running.")
        return {"job_id": job_id, "started": True}

    def forget(job_id: str = Body(..., embed=True)):
        _check(job_id)
        job = background_jobs.get_status(job_id)
        if job and job.get("status") in ("running", "queued"):
            raise ConflictError("That job is still running; cancel it first.")
        background_jobs.clear_job(job_id)   # drops the in-memory entry and its job_records row
        return {"job_id": job_id, "forgotten": True}

    app.add_api_route("/api/e2e/jobs/hold", hold, methods=["POST"])
    app.add_api_route("/api/e2e/jobs/forget", forget, methods=["POST"])
    # Ahead of any catch-all (the SPA fallback when the app serves the build).
    new = app.router.routes[-2:]
    del app.router.routes[-2:]
    app.router.routes[0:0] = new
    return app


def main():
    import uvicorn
    from api.api_config import ApiSettings
    from api.server import create_app

    install_e2e_stubs()
    # The key-free fake engine (tests/fake_engine.py): lets specs run a real
    # translation through the real API with no key or network.
    from tests import fake_engine
    fake_engine.install()
    # The push stream answers 429 here, so the client falls back to polling:
    # most specs mock GET /api/jobs/{id} and friends and expect them polled.
    # event-stream.spec.ts mocks /api/events itself. E2E_SSE=1 turns the real
    # stream on for a manual run.
    if os.environ.get("E2E_SSE") != "1":
        from services import event_stream_service
        event_stream_service.MAX_STREAMS = 0
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8611
    # A fixed, gitignored folder wiped at every start, rather than a temp
    # dir cleaned up on exit: Playwright may kill this process outright,
    # and a `finally` never runs then.
    library_dir = os.path.join(ROOT, "frontend", "test-results", "e2e-library")
    shutil.rmtree(library_dir, ignore_errors=True)
    os.makedirs(library_dir)
    seed(library_dir)
    app = add_e2e_job_routes(create_app(ApiSettings(port=port)))
    uvicorn.run(app, host="127.0.0.1", port=port,
                log_level="warning")


if __name__ == "__main__":
    main()
