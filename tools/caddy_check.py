"""Proves the household Caddyfile with a real Caddy, the `caddy` CI job (docs/testing-and-ci.md).

    python tools/caddy_check.py --caddy PATH_TO_CADDY

Renders deploy/caddy/Caddyfile.template the way the Setup service does
(installer/service_config.render_caddyfile) and runs `caddy validate` on it.
Then it serves the same file on a local HTTP port and sends raw request paths:
uncleaned ones (dot segments, doubled slashes, a backslash, decoded or %5c)
must get the template's 400, a clean one must reach the proxy.
tests/test_caddyfile_template.py checks the template's meaning with a small
reader; this checks that the Caddy the installer bundles reads it the same way.
"""
import argparse
import http.client
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "deploy" / "caddy" / "Caddyfile.template"
DOMAIN = "baihe.example.com"
UNCLEAN_PATHS = ("/api\\settings", "/api%5csettings", "/api%5Csettings", "/x/%5c%5cserver/share",
                 "/api/../settings", "/api/%2e%2e/settings", "//api/settings")
CLEAN_PATHS = ("/api/health", "/assets/app.js")
START_DEADLINE_SECONDS = 30
COMMAND_TIMEOUT_SECONDS = 60


def _render(log_dir):
    sys.path.insert(0, str(ROOT))
    from installer import service_config
    text = TEMPLATE.read_text(encoding="utf-8")
    return service_config.render_caddyfile(text, DOMAIN, service_config.DEFAULT_HOUSEHOLD_PORT, log_dir)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve_on_http(caddyfile, port):
    """The same file with its site served over plain HTTP on 127.0.0.1, so no certificate is asked for."""
    site = f"{DOMAIN} {{"
    if caddyfile.count(site) != 1:
        raise SystemExit(f"the rendered Caddyfile has no single '{site}' line")
    return caddyfile.replace(site, f"http://127.0.0.1:{port} {{")


def _status(port, raw_path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        # putrequest sends the path as given: no client-side cleaning or quoting.
        conn.putrequest("GET", raw_path, skip_host=True)
        conn.putheader("Host", f"127.0.0.1:{port}")
        conn.endheaders()
        return conn.getresponse().status
    finally:
        conn.close()


def _wait_listening(proc, port):
    deadline = time.monotonic() + START_DEADLINE_SECONDS
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise SystemExit(f"caddy run exited {proc.returncode} before it listened")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError:
            time.sleep(0.2)
    raise SystemExit(f"caddy did not listen on {port} within {START_DEADLINE_SECONDS} s")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--caddy", required=True)
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        # Caddy keeps its autosave and instance id under these (APPDATA on Windows), so a run leaves nothing behind.
        env = {**os.environ, "XDG_DATA_HOME": tmp, "XDG_CONFIG_HOME": tmp, "HOME": tmp, "APPDATA": tmp}
        rendered = _render(tmp)
        validate_file = Path(tmp, "Caddyfile")
        validate_file.write_text(rendered, encoding="utf-8")
        subprocess.run([args.caddy, "validate", "--config", str(validate_file), "--adapter", "caddyfile"],
                       env=env, check=True, timeout=COMMAND_TIMEOUT_SECONDS)
        print("caddy validate: the rendered Caddyfile is valid")

        port = _free_port()
        live_file = Path(tmp, "Caddyfile.http")
        live_file.write_text(_serve_on_http(rendered, port), encoding="utf-8")
        proc = subprocess.Popen([args.caddy, "run", "--config", str(live_file), "--adapter", "caddyfile"],
                                env=env)
        try:
            _wait_listening(proc, port)
            wrong = []
            for path in UNCLEAN_PATHS:
                got = _status(port, path)
                print(f"{path!r} -> {got}")
                if got != 400:
                    wrong.append(f"{path!r} answered {got}, expected 400")
            for path in CLEAN_PATHS:
                got = _status(port, path)
                print(f"{path!r} -> {got}")
                # 400 and 404 are the template's own refusals; anything else got as far as the proxy.
                if got in (400, 404):
                    wrong.append(f"{path!r} was refused at the proxy ({got})")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=COMMAND_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
    if wrong:
        print("\n".join(wrong), file=sys.stderr)
        return 1
    print("every uncleaned path got 400 and every clean path passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
