"""Real-app smoke test: `python -m tools.smoke_test [--keep]`.

Starts the real server (`python -m api`) on a throwaway data folder and drives
it over HTTP: create a title, transcribe a short clip with the tiny Whisper
model on the CPU, translate with the Echo engine, edit a line, export SRT,
back up and restore the library, cancel a running job, then restart with
sign-in on and check that a member is refused an admin route. It needs the
`faster-whisper` package and the `Systran/faster-whisper-tiny` model (downloaded
on first use, about 75 MB), and uses no key, GPU or paid engine.

The mocked suite cannot catch what only a real process shows: a route or
service import that fails at startup, a job that never leaves "running", a
restore that corrupts the library, a permission left open. Exit code 1 on the
first failed step, with the tail of the server log.
"""
import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

CLIP = REPO_ROOT / "assets" / "smoke" / "speech-zh.wav"
LOCAL = {"X-Baihe-Local": "1"}
START_TIMEOUT = 60
JOB_TIMEOUT = 150
REQUEST_TIMEOUT = 60
LOG_TAIL_LINES = 40
FINISHED = ("done", "failed", "cancelled")


# An HTTP_PROXY in the developer's environment would otherwise capture requests to loopback.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class SmokeFailure(Exception):
    pass


def check(condition, message):
    if not condition:
        raise SmokeFailure(message)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def multipart(fields: dict, file_field: str, filename: str, data: bytes):
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
                  f'filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
                 + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


class Server:
    """One `python -m api` process. Stopping uses SIGINT so the server runs the
    clean shutdown it runs for Ctrl+C; a process that ignores it is killed."""

    def __init__(self, data_dir: Path, log_path: Path, auth: bool):
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.log_path = log_path
        env = dict(os.environ)
        env.pop("BAIHE_API_HOUSEHOLD_PORT", None)
        env.update({
            "BAIHE_DATA_DIR": str(data_dir),
            "BAIHE_API_PORT": str(self.port),
            "BAIHE_API_BACKGROUND": "0",
            # The built frontend is not part of this test.
            "BAIHE_API_SERVE_FRONTEND": "0",
            # Pinned: development mode starts a reloader that stop() would orphan,
            # and a household port or another host would change what is tested.
            "BAIHE_API_ENV": "production",
            "BAIHE_API_HOST": "127.0.0.1",
            "BAIHE_API_AUTH": "on" if auth else "off",
            "BAIHE_API_COOKIE_SECURE": "0",
            "PYTHONUNBUFFERED": "1",
            "PYTHONUTF8": "1",
        })
        self._log = open(log_path, "ab")
        self.proc = subprocess.Popen([sys.executable, "-m", "api"], cwd=REPO_ROOT, env=env,
                                     stdout=self._log, stderr=subprocess.STDOUT)

    def wait_ready(self):
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline:
            check(self.proc.poll() is None, f"the server exited at startup (code {self.proc.returncode})")
            try:
                if self.request("GET", "/api/health")[0] == 200:
                    return
            except OSError:
                pass
            time.sleep(0.5)
        raise SmokeFailure(f"the server did not answer /api/health within {START_TIMEOUT} s")

    def request(self, method, path, body=None, form=None, headers=None, expect=None, code=None):
        """(status, headers, bytes). `body` is sent as JSON, `form` is
        (fields, file_field, filename, data) as multipart. With `expect`, any
        other status fails the step, and `code` is the error body's code."""
        send = dict(LOCAL if headers is None else headers)
        data = None
        if form is not None:
            data, send["Content-Type"] = multipart(*form)
        elif body is not None or method == "POST":
            data = json.dumps(body if body is not None else {}).encode()
            send["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=send)
        try:
            with _OPENER.open(req, timeout=REQUEST_TIMEOUT) as resp:
                status, hdrs, payload = resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as err:
            status, hdrs, payload = err.code, dict(err.headers), err.read()
        if code is not None:
            found = json.loads(payload or b"{}").get("error", {}).get("code")
            check(found == code, f"{method} {path} gave error code {found!r}, expected {code!r}")
        if expect is not None:
            check(status == expect, f"{method} {path} returned {status}, expected {expect}: "
                                    f"{payload[:200].decode('utf-8', 'replace')}")
        return status, hdrs, payload

    def json(self, method, path, body=None, form=None, expect=200):
        return json.loads(self.request(method, path, body, form, expect=expect)[2] or b"null")

    def wait_job(self, job_id: str, want="done", timeout=JOB_TIMEOUT):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.json("GET", f"/api/jobs/{job_id}")
            if job["status"] in FINISHED:
                check(job["status"] == want,
                      f"job {job_id} ended {job['status']}, expected {want}: {job.get('error') or job.get('message')}")
                return job
            time.sleep(1)
        raise SmokeFailure(f"job {job_id} was still running after {timeout} s")

    def stop(self):
        if self.proc.poll() is None:
            # Windows has no SIGINT to send to a child; terminate() is its stop.
            if sys.platform == "win32":
                self.proc.terminate()
            else:
                self.proc.send_signal(signal.SIGINT)
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self._log.close()

    def log_tail(self) -> str:
        try:
            lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        return "\n".join(lines[-LOG_TAIL_LINES:])


def lines_of(server, drama_id):
    return server.json("GET", f"/api/review/dramas/{drama_id}/lines")["lines"]


def run_owner_flows(server: Server, step):
    ctx = {}

    @step("Create a title")
    def _():
        server.json("POST", "/api/assistant/settings", {"developer_mode": True})
        # The step transcribes tiny Whisper on the CPU, which is no longer the fresh-folder default.
        server.json("POST", "/api/settings", {"use_gpu": False})
        engines = server.json("GET", "/api/translate/engines")["items"]
        check(any(e["name"] == "echo" for e in engines), "the Echo engine is not listed with Developer Mode on")
        drama = server.json("POST", "/api/dramas", {"source_language": "zh", "title_en": "Smoke test title"}, expect=201)
        ctx["id"] = drama["id"]
        server.json("POST", f"/api/source/dramas/{ctx['id']}/config", {"transcript_mode": "whisper"})
        server.json("POST", f"/api/transcribe/dramas/{ctx['id']}/config", {"whisper_size": "tiny"})

    @step("Transcribe with tiny Whisper on the CPU")
    def _():
        started = server.json("POST", f"/api/media/dramas/{ctx['id']}/upload-and-transcribe",
                              form=({"source_language": "zh"}, "file", "speech-zh.wav", CLIP.read_bytes()))
        job = server.wait_job(started["job_id"])
        ctx["transcribe_job"] = started["job_id"]
        check(job["result"]["run_settings"]["use_gpu"] is False, "the run did not use the CPU")
        got = lines_of(server, ctx["id"])
        check(got and all(l["zh"].strip() for l in got), "transcription produced no lines")
        ctx["line_count"] = len(got)

    @step("Translate with the Echo engine")
    def _():
        started = server.json("POST", f"/api/translate-run/dramas/{ctx['id']}/run", {"engine": "echo"})
        server.wait_job(started["job_id"])
        got = lines_of(server, ctx["id"])
        check(all(l["en"].startswith("[EN]") for l in got), "a line was left untranslated")

    @step("Edit a line and export SRT")
    def _():
        first = lines_of(server, ctx["id"])[0]
        server.json("POST", f"/api/lines/dramas/{ctx['id']}/lines/{first['id']}", {"en": "Edited by the smoke test"})
        status, hdrs, payload = server.request("GET", f"/api/export/dramas/{ctx['id']}/subtitle?fmt=srt&field=en",
                                               expect=200)
        srt = payload.decode("utf-8")
        check("Edited by the smoke test" in srt, "the edit is missing from the SRT")
        check(srt.count(" --> ") == ctx["line_count"], "the SRT has a different number of cues than the title has lines")
        ctx["edited_line"] = first["id"]

    @step("Back up, change a line, restore")
    def _():
        job = server.json("POST", "/api/library/admin/backup", {})
        server.wait_job(job["job_id"])
        _, _, archive = server.request("GET", "/api/library/admin/artifacts/backup", expect=200)
        check(archive[:2] == b"PK", "the backup is not a zip file")
        server.json("POST", f"/api/lines/dramas/{ctx['id']}/lines/{ctx['edited_line']}", {"en": "Changed after the backup"})
        server.json("POST", "/api/library/admin/restore",
                    form=({"confirm": "true", "confirm_text": "RESTORE"}, "file", "backup.zip", archive))
        restored = {l["id"]: l["en"] for l in lines_of(server, ctx["id"])}
        check(restored[ctx["edited_line"]] == "Edited by the smoke test",
              "the restore did not bring back the backed-up line")

    @step("Cancel a running job")
    def _():
        started = server.json("POST", f"/api/transcribe/dramas/{ctx['id']}/run", {"source_language": "zh"})
        server.json("POST", f"/api/jobs/{started['job_id']}/cancel", {})
        server.wait_job(started["job_id"], want="cancelled", timeout=60)
        check(lines_of(server, ctx["id"])[0]["en"] == "Edited by the smoke test",
              "the cancelled run changed the lines")


def run_member_flows(server: Server, step, member):
    import api.auth as auth

    cookie = {"Cookie": f"{auth.DEV_COOKIE_NAME}={member['session_token']}"}
    signed_in = {**LOCAL, **cookie}
    with_csrf = {**signed_in, auth.CSRF_HEADER: member["csrf_token"]}

    @step("Signed-out request is refused")
    def _():
        server.request("GET", "/api/jobs", expect=401)

    @step("Member can read but not use admin routes")
    def _():
        server.request("GET", "/api/jobs", headers=signed_in, expect=200)
        server.request("GET", "/api/library/admin/artifacts/backup/info", headers=with_csrf,
                       expect=403, code="forbidden")
        server.request("GET", "/api/library/admin/storage", headers=with_csrf, expect=403, code="forbidden")

    @step("Member write without the CSRF token is refused")
    def _():
        # The owner's real job would be refused by ownership too, so the code is what
        # proves the CSRF check fired; the same request with the token must get past it
        # (404 for a job that does not exist) or the refusal proves nothing.
        server.request("POST", "/api/jobs/transcribe_1/cancel", {}, headers=signed_in,
                       expect=403, code="csrf_failed")
        server.request("POST", "/api/jobs/no_such_job/cancel", {}, headers=with_csrf, expect=404)


def mint_member(library_dir: Path) -> dict:
    """A member with the default household permissions and a live session,
    written straight to the library the stopped server left behind."""
    import db
    from services import auth_service
    db.configure_library_dir(str(library_dir))
    user = auth_service.add_user("member@smoke.invalid")
    return auth_service.create_session(user["id"])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.smoke_test", description=__doc__.splitlines()[0])
    parser.add_argument("--keep", action="store_true", help="keep the throwaway data folder and print its location")
    args = parser.parse_args(argv)
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        print("FAIL faster-whisper is not installed (pip install -r requirements-media.txt)")
        return 1

    work = Path(tempfile.mkdtemp(prefix="baihe-smoke-"))
    data_dir = work / "data"
    data_dir.mkdir()
    results = []
    server = None
    started_at = time.monotonic()

    def step(name):
        def register(fn):
            began = time.monotonic()
            try:
                fn()
            except SmokeFailure as exc:
                results.append((name, False))
                print(f"FAIL {name}: {exc}", flush=True)
                raise
            results.append((name, True))
            print(f"PASS {name} ({time.monotonic() - began:.1f} s)", flush=True)
            return fn
        return register

    try:
        server = Server(data_dir, work / "owner.log", auth=False)
        server.wait_ready()
        run_owner_flows(server, step)
        server.stop()
        member = mint_member(data_dir / "library")
        server = Server(data_dir, work / "member.log", auth=True)
        server.wait_ready()
        run_member_flows(server, step, member)
    except SmokeFailure:
        if server is not None:
            print("--- server log (tail) ---\n" + server.log_tail(), flush=True)
        return 1
    finally:
        if server is not None:
            server.stop()
        if args.keep:
            print(f"Kept {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)
    print(f"{len(results)} steps passed in {time.monotonic() - started_at:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
