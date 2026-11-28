"""
installer/e2e_check.py -- CI only (.github/workflows/installer-e2e.yml; not
shipped): the installer and in-app update end-to-end test's helpers.

The in-app updater only talks https to api.github.com and github.com, with no
test hook, so the test stands up a fake GitHub the unchanged updater reaches
the way it reaches the real one: `serve` is an HTTPS proxy (HTTPS_PROXY,
which `requests` honours) that answers CONNECT to those two hosts itself, with
a certificate from a throwaway CA the app trusts only through
REQUESTS_CA_BUNDLE in its own environment. Nothing else on the runner is
redirected, and the proxy refuses every other host.

Release signing uses a throwaway Ed25519 key made on the runner (`keygen`),
never the project's release key: the test copies its public half into the
installed copy's installer/release_public_key.txt, so the copy under test
checks signatures exactly as a real one does.

Runner Python (with `cryptography`):
    certs DIR | keygen DIR | publish ... | serve ...
    setting DB KEY [--set JSON] | wait-setting DB KEY ...
    api METHOD PATH | wait-status ... | wait-download
Installed Python, with the installed app on sys.path (--app <install>\\app):
    seed | verify-seed | backup | update
"""

import argparse
import datetime
import hashlib
import http.server
import json
import os
import shutil
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

FAKE_HOSTS = ("api.github.com", "github.com")
SEED_TITLE = "E2E Example Title"
SEED_LINES = (("你好，世界。", "Hello, world."), ("再见。", "Goodbye."))
HTTP_TIMEOUT = 30


def _fail(message) -> int:
    print(f"FAIL: {message}", file=sys.stderr, flush=True)
    return 1


# --- runner side: certificates, keys, releases --------------------------------

def cmd_certs(args) -> int:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    out = Path(args.dir)
    out.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key, leaf_key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Baihe E2E Throwaway CA")])
    ca_ski = x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key())
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - datetime.timedelta(days=1))
          .not_valid_after(now + datetime.timedelta(days=2))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False,
                                       key_encipherment=False, data_encipherment=False,
                                       key_agreement=False, key_cert_sign=True, crl_sign=True,
                                       encipher_only=False, decipher_only=False), critical=True)
          .add_extension(ca_ski, critical=False)
          .sign(ca_key, hashes.SHA256()))
    leaf = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, FAKE_HOSTS[0])]))
            .issuer_name(ca_name).public_key(leaf_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=2))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(h) for h in FAKE_HOSTS]),
                           critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                                         key_encipherment=True, data_encipherment=False,
                                         key_agreement=False, key_cert_sign=False, crl_sign=False,
                                         encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ca_ski),
                           critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()),
                           critical=False)
            .sign(ca_key, hashes.SHA256()))
    pem = serialization.Encoding.PEM
    (out / "ca.pem").write_bytes(ca.public_bytes(pem))
    (out / "leaf.pem").write_bytes(leaf.public_bytes(pem) + ca.public_bytes(pem))
    (out / "leaf.key").write_bytes(leaf_key.private_bytes(
        pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    print(f"Wrote a throwaway CA and a certificate for {', '.join(FAKE_HOSTS)}.")
    return 0


def _repo_lib():
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from lib import release_signature
    return release_signature


def cmd_keygen(args) -> int:
    rs = _repo_lib()
    out = Path(args.dir)
    out.mkdir(parents=True, exist_ok=True)
    private, public = rs.generate()
    # Never printed: the job log is public.
    (out / "test_release_private_key.txt").write_text(private + "\n", encoding="ascii")
    (out / "release_public_key.txt").write_text(
        "# Throwaway key made by the installer-e2e CI job; not the project's release key.\n"
        + public + "\n", encoding="ascii")
    print(f"Made a throwaway release key: {public}")
    return 0


def _sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_publish(args) -> int:
    """Makes `--file` the only release the fake GitHub lists, as v<version>
    with BaiheStudio-Setup-<version>.exe, its .sha256 and its .sig."""
    rs = _repo_lib()
    root = Path(args.root)
    tag = f"v{args.version}"
    name = f"BaiheStudio-Setup-{args.version}.exe"
    assets = root / "assets" / tag
    assets.mkdir(parents=True, exist_ok=True)
    exe = assets / name
    shutil.copyfile(args.file, exe)
    sha = _sha256(exe)
    (assets / (name + ".sha256")).write_text(f"{sha}  {name}\n", encoding="ascii")
    seed = rs.parse_private_key(Path(args.key).read_text(encoding="ascii"))
    (assets / (name + ".sig")).write_text(rs.format_signature(rs.sign(seed, name, sha)),
                                          encoding="ascii")
    base = f"https://github.com/{args.repo}/releases/download/{tag}/"
    release = {"tag_name": tag, "name": f"Baihe Studio {args.version}", "draft": False,
               "prerelease": False, "body": "Installer end-to-end test release.",
               "assets": [{"name": n, "size": (assets / n).stat().st_size,
                           "browser_download_url": base + n}
                          for n in (name, name + ".sha256", name + ".sig")]}
    (root / "releases.json").write_text(json.dumps([release]), encoding="utf-8")
    print(f"Fake GitHub now lists {tag} ({name}, sha256 {sha}).")
    return 0


# --- runner side: the fake GitHub ---------------------------------------------

class _FakeGitHub(http.server.BaseHTTPRequestHandler):
    timeout = 120

    def log_message(self, fmt, *a):
        headers = getattr(self, "headers", None)
        print(f"{headers.get('Host', '?') if headers else '?'} {fmt % a}", flush=True)

    def _send_file(self, path, ctype):
        size = path.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.end_headers()
        with open(path, "rb") as fh:
            shutil.copyfileobj(fh, self.wfile, 1 << 20)

    def do_GET(self):
        root = self.server.root
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        path = self.path.split("?")[0]
        repo = self.server.repo
        if host == "api.github.com" and path == f"/repos/{repo}/releases":
            return self._send_file(root / "releases.json", "application/json")
        prefix = f"/{repo}/releases/download/"
        if host == "github.com" and path.startswith(prefix):
            tag, _, name = path[len(prefix):].partition("/")
            target = root / "assets" / tag / name
            if "/" not in name and ".." not in tag and target.is_file():
                return self._send_file(target, "application/octet-stream")
        self.send_error(404)


class _Proxy(http.server.BaseHTTPRequestHandler):
    timeout = 120

    def log_message(self, fmt, *a):
        print(f"proxy {fmt % a}", flush=True)

    def do_CONNECT(self):
        host, _, port = self.path.rpartition(":")
        if host.lower() not in FAKE_HOSTS or port != "443":
            self.send_error(502, "not a host this test fakes")
            return
        self.send_response(200, "Connection established")
        self.end_headers()
        self.close_connection = True
        try:
            tls = self.server.tls.wrap_socket(self.connection, server_side=True)
        except (ssl.SSLError, OSError) as exc:
            print(f"proxy TLS handshake failed: {exc}", flush=True)
            return
        try:
            _FakeGitHub(tls, self.client_address, self.server)
        except OSError as exc:
            print(f"proxy tunnel ended: {exc}", flush=True)
        finally:
            tls.close()


def cmd_serve(args) -> int:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), _Proxy)
    server.daemon_threads = True
    server.root = Path(args.root)
    server.repo = args.repo
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(args.cert, args.key)
    server.tls = ctx
    print(f"Fake GitHub proxy on 127.0.0.1:{args.port}", flush=True)
    server.serve_forever()
    return 0


# --- runner side: settings and the API ----------------------------------------

def _get_setting(db, key):
    conn = sqlite3.connect(db, timeout=30)
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    finally:
        conn.close()
    return json.loads(row[0]) if row else None


def cmd_setting(args) -> int:
    if args.set is not None:
        value = json.loads(args.set)
        conn = sqlite3.connect(args.db, timeout=30)
        try:
            conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                         (args.key, json.dumps(value)))
            conn.commit()
        finally:
            conn.close()
    print(json.dumps(_get_setting(args.db, args.key)))
    return 0


def cmd_wait_setting(args) -> int:
    """Waits until the setting holds `--equals`, or (a number, or a record's
    `at`) is below `--below` / above `--above`."""
    deadline = time.time() + args.timeout
    value = None
    while time.time() < deadline:
        value = _get_setting(args.db, args.key)
        number = value.get("at") if isinstance(value, dict) else value
        if args.equals is not None and value == json.loads(args.equals):
            break
        if isinstance(number, (int, float)) and not isinstance(number, bool):
            if args.below is not None and number < args.below:
                break
            if args.above is not None and number > args.above:
                break
        time.sleep(1)
    else:
        return _fail(f"{args.key} is {json.dumps(value)} after {args.timeout} s")
    print(f"{args.key} = {json.dumps(value)}")
    return 0


def _request(method, path, port):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    data = b"{}" if method == "POST" else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json",
                                          "X-Baihe-Local": "1"})
    try:
        with opener.open(req, timeout=HTTP_TIMEOUT) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            return exc.code, json.loads(body or b"null")
        except ValueError:
            return exc.code, body.decode("utf-8", "replace")


def cmd_api(args) -> int:
    status, body = _request(args.method, args.path, args.port)
    print(json.dumps({"status": status, "body": body}))
    if args.expect is not None and status != args.expect:
        return _fail(f"{args.method} {args.path} answered {status}, expected {args.expect}")
    return 0


UPDATE_STATUS = "/api/system/update"


def cmd_wait_status(args) -> int:
    deadline = time.time() + args.timeout
    last = None
    while time.time() < deadline:
        try:
            status, body = _request("GET", UPDATE_STATUS, args.port)
            last = body
            if status == 200 and isinstance(body, dict) and body.get(args.field) == args.equals:
                print(f"{args.field} = {args.equals}")
                return 0
        except (OSError, ValueError) as exc:
            last = str(exc)
        time.sleep(2)
    return _fail(f"{args.field} never became {args.equals!r}; last answer: {last}")


def cmd_wait_download(args) -> int:
    deadline = time.time() + args.timeout
    body = None
    while time.time() < deadline:
        _, body = _request("GET", UPDATE_STATUS, args.port)
        if isinstance(body, dict) and body.get("download") in ("verified", "failed"):
            break
        time.sleep(2)
    if not isinstance(body, dict) or body.get("download") != "verified":
        return _fail(f"the download didn't verify: {body}")
    if body.get("verified_version") != args.version or not body.get("signature_check") \
            or not body.get("signature_verified"):
        return _fail(f"verified, but not as a signed {args.version}: {body}")
    print(f"Downloaded and verified {body.get('verified_name')}, signature checked.")
    return 0


# --- installed side ------------------------------------------------------------

def _use_app(app):
    app = str(Path(app).resolve())
    if app not in sys.path:
        sys.path.insert(0, app)
    os.chdir(app)


def cmd_seed(args) -> int:
    _use_app(args.app)
    import core
    import db
    drama_id = db.create_drama(title_zh="示例标题", title_en=SEED_TITLE)
    lines = [core.Line(idx=i, start=float(i), end=i + 0.9, zh=zh, en=en)
             for i, (zh, en) in enumerate(SEED_LINES)]
    db.save_lines(drama_id, lines)
    print(drama_id)
    return 0


def cmd_verify_seed(args) -> int:
    _use_app(args.app)
    import db
    drama = db.get_drama(args.id)
    if drama is None or drama["title_en"] != SEED_TITLE:
        return _fail(f"title {args.id} is gone or changed: {drama and dict(drama)}")
    got = [(line["zh"], line["en"]) for line in db.load_lines(args.id)]
    if got != list(SEED_LINES):
        return _fail(f"title {args.id}'s lines changed: {got}")
    print(f"Title {args.id} and its {len(got)} lines survived.")
    return 0


def cmd_backup(args) -> int:
    """A library backup: the full installer's backup gate needs a recent one
    when it can't read the release's schema hash (no app-only manifest)."""
    _use_app(args.app)
    from services import auto_backup_service
    auto_backup_service.start_now(include_media=False)
    deadline = time.time() + args.timeout
    while auto_backup_service.job_running() and time.time() < deadline:
        time.sleep(1)
    last = auto_backup_service.settings_overview().get("last_run_at")
    if not last:
        return _fail("the backup didn't finish")
    print(f"Backed up the library at {last}.")
    return 0


def cmd_update(args) -> int:
    """The updater's own check, download and verify, then its silent Setup
    (install(silent=True), what the automatic update runs). Exits as soon as
    Setup is started: this process holds the installed Python's files, which
    Setup replaces."""
    _use_app(args.app)
    from services import update_service as us
    us.check()
    st = us.status()
    if st.get("latest") != args.version or not st.get("update_available"):
        return _fail(f"the check didn't offer {args.version}: {st}")
    us.download()
    st = us.status()
    if not st.get("verified") or not st.get("signature_check"):
        return _fail(f"the download wasn't verified with the release key: {st}")
    if us._verified is None or us._verified.get("signature") is None:
        return _fail("the download was verified without its signature")
    result = us.install(silent=True)
    print(json.dumps({"setup_pid": us._setup.pid, **result}), flush=True)
    os._exit(0)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("certs")
    s.add_argument("dir")
    s.set_defaults(func=cmd_certs)
    s = sub.add_parser("keygen")
    s.add_argument("dir")
    s.set_defaults(func=cmd_keygen)
    s = sub.add_parser("publish")
    for a in ("--root", "--repo", "--version", "--file", "--key"):
        s.add_argument(a, required=True)
    s.set_defaults(func=cmd_publish)
    s = sub.add_parser("serve")
    for a in ("--root", "--repo", "--cert", "--key"):
        s.add_argument(a, required=True)
    s.add_argument("--port", type=int, required=True)
    s.set_defaults(func=cmd_serve)
    s = sub.add_parser("setting")
    s.add_argument("db")
    s.add_argument("key")
    s.add_argument("--set")
    s.set_defaults(func=cmd_setting)
    s = sub.add_parser("wait-setting")
    s.add_argument("db")
    s.add_argument("key")
    s.add_argument("--equals")
    s.add_argument("--below", type=float)
    s.add_argument("--above", type=float)
    s.add_argument("--timeout", type=float, default=60)
    s.set_defaults(func=cmd_wait_setting)
    s = sub.add_parser("api")
    s.add_argument("method", choices=("GET", "POST"))
    s.add_argument("path")
    s.add_argument("--port", type=int, default=8600)
    s.add_argument("--expect", type=int)
    s.set_defaults(func=cmd_api)
    s = sub.add_parser("wait-status")
    s.add_argument("--field", required=True)
    s.add_argument("--equals", required=True)
    s.add_argument("--port", type=int, default=8600)
    s.add_argument("--timeout", type=float, default=180)
    s.set_defaults(func=cmd_wait_status)
    s = sub.add_parser("wait-download")
    s.add_argument("--version", required=True)
    s.add_argument("--port", type=int, default=8600)
    s.add_argument("--timeout", type=float, default=300)
    s.set_defaults(func=cmd_wait_download)
    for name, func in (("seed", cmd_seed), ("verify-seed", cmd_verify_seed),
                       ("backup", cmd_backup), ("update", cmd_update)):
        s = sub.add_parser(name)
        s.add_argument("--app", required=True)
        if name == "verify-seed":
            s.add_argument("--id", type=int, required=True)
        if name == "update":
            s.add_argument("--version", required=True)
        if name == "backup":
            s.add_argument("--timeout", type=float, default=300)
        s.set_defaults(func=func)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
