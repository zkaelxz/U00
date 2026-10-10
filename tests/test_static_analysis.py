"""
tests/test_static_analysis.py -- static checks that catch bugs no
compile check or unit test can: every outbound HTTP call has a timeout=,
and the requirements/constraints files stay consistent with the launchers.
"""
import ast
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


_HTTP_VERBS = ("post", "get", "put", "patch", "delete", "head", "request")


def _find_requests_calls_missing_timeout(path, session_verbs=False):
    """Every network call in `path` that has no `timeout=` keyword:
    requests.<verb>() / session.request(), and urllib's urlopen().
    A hung server on one of these leaves a background job stuck at
    "running" forever -- a real, shipped gap this checks for directly
    rather than trusting every call site to remember it. Only
    unambiguous names are matched (`requests.<verb>`, `session.request`,
    `urlopen`), so ordinary `dict.get` / router `.post` decorators aren't.
    With `session_verbs=True` (used for services/), `session.<verb>` and
    `<name>.<verb>` on a name assigned from `requests.Session()` are
    checked too.

    Retired when no raw `requests`/`httpx`/`urlopen` call remains outside
    `lib/http.py` and `engine_backends/` (lib.http always passes timeout=)."""
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    problems = []
    session_names = set()
    if session_verbs:
        session_names.add("session")
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Attribute)
                    and node.value.func.attr == "Session"):
                session_names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if (session_verbs and isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                and f.value.id in session_names and f.attr in _HTTP_VERBS):
            if not any(kw.arg == "timeout" for kw in node.keywords):
                problems.append(node.lineno)
            continue
        is_http = (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                   and ((f.value.id == "requests" and f.attr in _HTTP_VERBS)
                        or (f.value.id == "session" and f.attr == "request")))
        is_http = is_http or (isinstance(f, ast.Attribute) and f.attr == "urlopen") \
            or (isinstance(f, ast.Name) and f.id == "urlopen")
        if is_http and not any(kw.arg == "timeout" for kw in node.keywords):
            problems.append(node.lineno)
    return problems


def _py_files_under(dirname):
    out = []
    for root, dirs, files in os.walk(os.path.join(PROJECT_ROOT, dirname)):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        out += [os.path.join(root, f) for f in files if f.endswith(".py")]
    return sorted(out)


class TestHttpCallsHaveTimeouts:
    """Regression coverage for a real gap: translate_engines.py's Gemini,
    Google, and Ollama calls, and qa.py's Gemini call, had no timeout=
    at all. Without one, a server that stops responding mid-request
    leaves the job stuck at "running" with no way to notice."""

    def test_translate_engines(self):
        files = [os.path.join(PROJECT_ROOT, "translate_engines.py")] + _py_files_under("engine_backends")
        problems = {os.path.relpath(f, PROJECT_ROOT): _find_requests_calls_missing_timeout(f)
                    for f in files}
        problems = {f: lines for f, lines in problems.items() if lines}
        assert problems == {}, f"requests call(s) missing timeout= at line(s): {problems}"

    def test_qa(self):
        problems = _find_requests_calls_missing_timeout(os.path.join(PROJECT_ROOT, "qa.py"))
        assert problems == [], f"requests call(s) missing timeout= at line(s): {problems}"

    def test_bulk_translate(self):
        problems = _find_requests_calls_missing_timeout(
            os.path.join(PROJECT_ROOT, "bulk_translate.py"))
        assert problems == [], f"requests call(s) missing timeout= at line(s): {problems}"

    def test_dub(self):
        problems = _find_requests_calls_missing_timeout(os.path.join(PROJECT_ROOT, "dub.py"))
        assert problems == [], f"requests call(s) missing timeout= at line(s): {problems}"

    def test_navigator(self):
        problems = _find_requests_calls_missing_timeout(os.path.join(PROJECT_ROOT, "navigator.py"))
        assert problems == [], f"requests call(s) missing timeout= at line(s): {problems}"

    def test_page_fetch(self):
        problems = _find_requests_calls_missing_timeout(os.path.join(PROJECT_ROOT, "page_fetch.py"))
        assert problems == [], f"requests call(s) missing timeout= at line(s): {problems}"

    def test_title_library(self):
        problems = _find_requests_calls_missing_timeout(
            os.path.join(PROJECT_ROOT, "title_library.py"))
        assert problems == [], f"requests call(s) missing timeout= at line(s): {problems}"

    def test_core(self):
        problems = _find_requests_calls_missing_timeout(os.path.join(PROJECT_ROOT, "core.py"))
        assert problems == [], f"requests call(s) missing timeout= at line(s): {problems}"

    def test_diagnostics(self):
        # Step 27: get_latest_pypi_version's requests.get to PyPI's JSON API.
        problems = _find_requests_calls_missing_timeout(
            os.path.join(PROJECT_ROOT, "diagnostics.py"))
        assert problems == [], f"requests call(s) missing timeout= at line(s): {problems}"


    def test_asmr_vad_model_download(self):
        problems = _find_requests_calls_missing_timeout(os.path.join(PROJECT_ROOT, "asmr_vad.py"))
        assert problems == [], f"requests call(s) missing timeout= at line(s): {problems}"

    def test_memory_headroom(self):
        problems = _find_requests_calls_missing_timeout(
            os.path.join(PROJECT_ROOT, "memory_headroom.py"))
        assert problems == [], f"requests call(s) missing timeout= at line(s): {problems}"

    def test_sources_http_and_dictionary(self):
        # sources/http.py (session.request) and dictionary.py (urlopen).
        for name in ("sources/http.py", "dictionary.py"):
            problems = _find_requests_calls_missing_timeout(os.path.join(PROJECT_ROOT, name))
            assert problems == [], f"{name}: call(s) missing timeout= at line(s): {problems}"

    def test_url_import_modules(self):
        # Sources S-4/S-5 and the URL download: the modules those routes
        # reach outside services/ and api/.
        for name in ("video_download.py", "sources/pipeline.py", "sources/front_door.py",
                     "sources/generic_import.py", "sources/store.py", "sources/adaptive.py",
                     "sources/novel_follow.py", "sources/domains.py"):
            problems = _find_requests_calls_missing_timeout(os.path.join(PROJECT_ROOT, name))
            assert problems == [], f"{name}: call(s) missing timeout= at line(s): {problems}"

    def test_live_fetch(self):
        # The live stream fetcher's GETs run on a background thread for the
        # whole session; its stall handling relies on the read timeout.
        path = os.path.join(PROJECT_ROOT, "live_fetch.py")
        assert _find_requests_calls_missing_timeout(path, session_verbs=True) == []
        assert "session.get(" in open(path, encoding="utf-8").read(), \
            "the timeout check no longer sees the fetcher's GET"

    def test_installer_service_helper(self):
        # installer/service.py's loopback health check goes through an
        # opener's .open(), which the name-based check above doesn't match,
        # so every .open( call is checked for a timeout here too.
        path = os.path.join(PROJECT_ROOT, "installer", "service.py")
        assert _find_requests_calls_missing_timeout(path) == []
        src = open(path, encoding="utf-8").read()
        opens = re.findall(r"\.open\([^)]*\)", src)
        assert opens, "the check no longer sees the health call"
        assert all("timeout=" in call for call in opens), opens

    def test_source_probe_script(self):
        # The manual reachability probe makes real GETs (session.request).
        problems = _find_requests_calls_missing_timeout(
            os.path.join(PROJECT_ROOT, "scripts", "source_probe.py"), session_verbs=True)
        assert problems == [], f"call(s) missing timeout= at line(s): {problems}"
        src = open(os.path.join(PROJECT_ROOT, "scripts", "source_probe.py"),
                   encoding="utf-8").read()
        assert "session.request(" in src, "the timeout check no longer sees the probe's GET"

    def test_notification_service(self):
        # Step 44: the Discord/ntfy push runs from a timer thread after a
        # job ends; a hung webhook must never hold it (Session.post checked).
        problems = _find_requests_calls_missing_timeout(
            os.path.join(PROJECT_ROOT, "services", "notification_service.py"), session_verbs=True)
        assert problems == [], f"call(s) missing timeout= at line(s): {problems}"
        src = open(os.path.join(PROJECT_ROOT, "services", "notification_service.py"),
                   encoding="utf-8").read()
        assert "session.post(" in src, "the timeout check no longer sees the send call"

    def test_services_and_api_packages(self):
        # B-07: the FastAPI layer's services/ (metadata autofill's page
        # fetch, etc.) and api/ must never make an untimed HTTP call.
        files = (_py_files_under("services") + _py_files_under("api")
                 + _py_files_under("lib"))
        assert files, "services/, api/ and lib/ were not found"
        # lib/http.py's pinned_get is the one raw requests call outside engine_backends/.
        assert os.path.join(PROJECT_ROOT, "lib", "http.py") in files
        problems = {os.path.relpath(f, PROJECT_ROOT):
                    _find_requests_calls_missing_timeout(f, session_verbs=True)
                    for f in files}
        problems = {k: v for k, v in problems.items() if v}
        assert problems == {}, f"call(s) missing timeout=: {problems}"


_HTTPX_CLIENTS = ("OAuth2Client", "AsyncOAuth2Client", "Client", "AsyncClient")
_HTTPX_CALLS = _HTTP_VERBS + ("stream", "fetch_token")


def _find_httpx_calls_missing_timeout(path):
    """(lineno list, number of calls checked) for httpx / Authlib client use
    in `path`: every `OAuth2Client(...)`, `httpx.Client(...)`,
    `httpx.<verb>(...)` and `<client>.<verb>/fetch_token(...)` on a name
    bound by `with ... as <client>` must pass `timeout=`."""
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    clients = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.withitem) and isinstance(node.optional_vars, ast.Name):
            clients.add(node.optional_vars.id)
    problems, checked = [], 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
        owner = f.value.id if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) else ""
        is_client = (not owner and name in ("OAuth2Client", "AsyncOAuth2Client")) or \
            (owner == "httpx" and name in _HTTPX_CLIENTS)
        is_call = (owner == "httpx" and name in _HTTPX_CALLS) or \
            (owner in clients and name in _HTTPX_CALLS)
        if is_client or is_call:
            checked += 1
            if not any(kw.arg == "timeout" for kw in node.keywords):
                problems.append(node.lineno)
    return sorted(problems), checked


class TestSignInHttpTimeouts:
    """Step 134: Google sign-in talks to Google with Authlib's httpx
    OAuth2Client and httpx, which the requests-based check above can't see."""

    def test_oidc_service(self):
        problems, checked = _find_httpx_calls_missing_timeout(
            os.path.join(PROJECT_ROOT, "services", "oidc_service.py"))
        assert checked >= 4, "the checker no longer sees oidc_service's HTTP calls"
        assert problems == [], f"httpx/Authlib call(s) missing timeout= at line(s): {problems}"

    def test_checker_catches_missing_timeouts(self, tmp_path):
        p = tmp_path / "mod.py"
        p.write_text("import httpx\n"
                     "with OAuth2Client(client_id=1) as c:\n"
                     "    c.fetch_token(u, code=1)\n"
                     "with httpx.Client(timeout=5) as h:\n"
                     "    h.get(u, timeout=5)\n"
                     "httpx.get(u)\n")
        assert _find_httpx_calls_missing_timeout(str(p)) == ([2, 3, 6], 5)


def _find_socket_calls_missing_timeout(path):
    """(lineno list, number checked) for `socket.create_connection(...)`
    without `timeout=`, and for `socket.socket()` bound in a `with` whose
    body never calls `.settimeout(`: a silent peer would otherwise block
    the caller forever."""
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    problems, checked = [], 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "socket" \
                and node.func.attr == "create_connection":
            checked += 1
            if not any(kw.arg == "timeout" for kw in node.keywords) and len(node.args) < 2:
                problems.append(node.lineno)
        if isinstance(node, ast.With):
            for item in node.items:
                c = item.context_expr
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) \
                        and isinstance(c.func.value, ast.Name) and c.func.value.id == "socket" \
                        and c.func.attr == "socket":
                    checked += 1
                    if not any(isinstance(n, ast.Attribute) and n.attr == "settimeout"
                               for b in node.body for n in ast.walk(b)):
                        problems.append(node.lineno)
    return sorted(problems), checked


class TestInstallerAndRemoteHealthTimeouts:
    def test_installer_launcher(self):
        path = os.path.join(PROJECT_ROOT, "installer", "launcher.py")
        assert _find_requests_calls_missing_timeout(path) == []
        src = open(path, encoding="utf-8").read()
        opens = re.findall(r"_OPENER\.open\([^)]*\)", src)
        assert opens, "the check no longer sees the launcher's health call"
        assert all("timeout=" in call for call in opens), opens
        problems, checked = _find_socket_calls_missing_timeout(path)
        assert checked >= 1, "the checker no longer sees the launcher's port probe"
        assert problems == [], f"socket use without a timeout at line(s): {problems}"

    def test_remote_health_service_sockets(self):
        path = os.path.join(PROJECT_ROOT, "services", "remote_health_service.py")
        problems, checked = _find_socket_calls_missing_timeout(path)
        assert checked >= 2, "the checker no longer sees the TLS and listener connections"
        assert problems == [], f"socket use without a timeout at line(s): {problems}"

    def test_socket_checker_catches_missing_timeouts(self, tmp_path):
        p = tmp_path / "mod.py"
        p.write_text("import socket\n"
                     "socket.create_connection((h, 1))\n"
                     "socket.create_connection((h, 1), timeout=3)\n"
                     "with socket.socket() as s:\n"
                     "    s.connect_ex(a)\n"
                     "with socket.socket() as s:\n"
                     "    s.settimeout(1)\n")
        assert _find_socket_calls_missing_timeout(str(p)) == ([2, 4], 4)

    def test_httpx_calls_outside_oidc_service_have_timeouts(self):
        problems = {}
        for f in _project_py_files():
            # The client-name heuristic would flag any `with ... as x` file,
            # so only files that import httpx or Authlib are checked.
            if not re.search(r"^\s*(import|from)\s+(httpx|authlib)\b",
                             open(f, encoding="utf-8").read(), re.M):
                continue
            bad, _ = _find_httpx_calls_missing_timeout(f)
            if bad:
                problems[os.path.relpath(f, PROJECT_ROOT)] = bad
        assert problems == {}, f"httpx/Authlib call(s) missing timeout=: {problems}"


_SDK_CLIENTS = ("Anthropic", "AsyncAnthropic", "OpenAI", "AsyncOpenAI")


def _find_sdk_clients_missing_timeout(path):
    """(lineno list, number checked) for LLM SDK client constructors in
    `path` -- `Anthropic(...)`, `anthropic.Anthropic(...)`, `OpenAI(...)`
    and their Async forms -- that pass no `timeout=` (B-07)."""
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    problems, checked = [], 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
        if name in _SDK_CLIENTS:
            checked += 1
            if not any(kw.arg == "timeout" for kw in node.keywords):
                problems.append(node.lineno)
    return sorted(problems), checked


def _project_py_files():
    skip = {"__pycache__", "tests", "node_modules", "frontend", ".git", "venv", ".venv"}
    out = []
    for root, dirs, files in os.walk(PROJECT_ROOT):
        dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
        out += [os.path.join(root, f) for f in files if f.endswith(".py")]
    return sorted(out)


class TestSdkClientsHaveTimeouts:
    """B-07: the Anthropic and OpenAI SDK clients were built without
    timeout=, unlike every REST engine."""

    def test_every_sdk_client_has_a_timeout(self):
        problems, checked = {}, 0
        for f in _project_py_files():
            bad, n = _find_sdk_clients_missing_timeout(f)
            checked += n
            if bad:
                problems[os.path.relpath(f, PROJECT_ROOT)] = bad
        assert checked >= 2, "the checker no longer sees translate_engines' SDK clients"
        assert problems == {}, f"SDK client(s) missing timeout=: {problems}"

    def test_checker_catches_missing_timeouts(self, tmp_path):
        p = tmp_path / "mod.py"
        p.write_text("import anthropic\nfrom openai import OpenAI\n"
                     "a = anthropic.Anthropic(api_key=k)\n"
                     "b = OpenAI(api_key=k, timeout=5)\n"
                     "c = AsyncOpenAI(api_key=k)\n")
        assert _find_sdk_clients_missing_timeout(str(p)) == ([3, 5], 3)


class TestTimeoutCheckerItself:
    def test_catches_a_call_with_no_timeout(self, tmp_path):
        src = "import requests\nrequests.post(url, json={})\n"
        p = tmp_path / "mod.py"
        p.write_text(src)
        assert _find_requests_calls_missing_timeout(str(p)) == [2]

    def test_does_not_flag_a_call_with_timeout(self, tmp_path):
        src = "import requests\nrequests.post(url, json={}, timeout=30)\n"
        p = tmp_path / "mod.py"
        p.write_text(src)
        assert _find_requests_calls_missing_timeout(str(p)) == []

    def test_catches_urlopen_and_session_request_without_timeout(self, tmp_path):
        src = ("import urllib.request\nurllib.request.urlopen(u)\n"
               "session.request('GET', u)\nrequests.put(u)\n")
        p = tmp_path / "mod.py"
        p.write_text(src)
        assert _find_requests_calls_missing_timeout(str(p)) == [2, 3, 4]

    def test_accepts_urlopen_and_session_request_with_timeout(self, tmp_path):
        src = ("urllib.request.urlopen(u, timeout=5)\n"
               "session.request('GET', u, timeout=5)\n")
        p = tmp_path / "mod.py"
        p.write_text(src)
        assert _find_requests_calls_missing_timeout(str(p)) == []

    def test_ignores_unrelated_get_post_calls(self, tmp_path):
        src = "session.get(url)\nsome_dict.get('key')\n"
        p = tmp_path / "mod.py"
        p.write_text(src)
        assert _find_requests_calls_missing_timeout(str(p)) == []


class TestSessionVerbTimeouts:
    def test_flags_session_verbs_in_strict_mode_only(self, tmp_path):
        p = tmp_path / "mod.py"
        p.write_text("import requests\ns = requests.Session()\ns.get(u)\n"
                     "session.post(u)\ns.get(u, timeout=3)\nd.get('k')\n")
        assert _find_requests_calls_missing_timeout(str(p), session_verbs=True) == [3, 4]
        assert _find_requests_calls_missing_timeout(str(p)) == []

    def test_safe_fetch_module_is_scanned(self):
        path = os.path.join(PROJECT_ROOT, "services", "safe_fetch.py")
        assert path in _py_files_under("services")
        assert _find_requests_calls_missing_timeout(path, session_verbs=True) == []


class TestConstraintsFile:
    """Regression coverage for a real gap: requirements files only give
    minimum versions (>=), so a fresh install could silently pull in a
    new major version -- exactly how pyannote 4 broke diarization.
    constraints.txt pins known-risky packages' major versions; this
    checks it exists, caps what it's supposed to, and that the README's
    install command actually uses it."""

    def _read_constraints(self):
        path = os.path.join(PROJECT_ROOT, "constraints.txt")
        assert os.path.exists(path), "constraints.txt is missing"
        return open(path, encoding="utf-8").read()

    def test_caps_the_packages_known_to_have_broken_before(self):
        text = self._read_constraints()
        for pinned in ("pyannote.audio<5", "transformers<6", "torch<3",
                       "torchaudio<3", "faster-whisper<2"):
            assert pinned in text, f"missing pin: {pinned}"

    def test_urllib3_has_the_2_6_floor(self):
        """Security review L-1: requests alone allows urllib3 1.26 (no
        read1, CVE-2025-66471); the floor is in the requirements, the
        constraints and both launchers' "already installed?" checks."""
        assert "urllib3>=2.6" in self._read_constraints()
        core = open(os.path.join(PROJECT_ROOT, "requirements-core.txt"), encoding="utf-8").read()
        assert "urllib3>=2.8" in core
        for launcher in ("start.bat", "start.ps1"):
            text = open(os.path.join(PROJECT_ROOT, launcher), encoding="utf-8").read()
            assert ">= (2, 6)" in text, launcher

    def test_yt_dlp_and_edge_tts_are_left_uncapped(self):
        """Both need to stay current against sites/services that change
        often -- capping them would trade a fixable problem for a worse,
        deliberately-frozen one. Checks only actual pin lines, not the
        file's own comments explaining why they're absent."""
        pin_lines = [line for line in self._read_constraints().splitlines()
                     if line.strip() and not line.strip().startswith("#")]
        for line in pin_lines:
            assert not line.lower().startswith("yt-dlp"), line
            assert not line.lower().startswith("edge-tts"), line

    def test_readme_install_command_uses_constraints_txt(self):
        readme = open(os.path.join(PROJECT_ROOT, "README.md"), encoding="utf-8").read()
        assert "-c constraints.txt" in readme


def _requirements_package_names(filename):
    """Bare package names (lowercased, version specifiers/markers/comments
    stripped) from a requirements file, skipping `-r other-file.txt`
    pointer lines."""
    path = os.path.join(PROJECT_ROOT, filename)
    names = set()
    for line in open(path, encoding="utf-8"):
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-r"):
            continue
        name = re.split(r"[<>=!~\[\s]", line, 1)[0].strip()
        if name:
            names.add(name.lower())
    return names


class TestRequirementsFilesConsolidation:
    """Regression coverage for Step 75: `requirements.txt` (the old flat,
    43-package file used by manual installs) and the tiered
    requirements-core/media/optional split (what `start.bat` and the
    Diagnostics tab actually install from) had drifted apart with real
    content missing from each -- the flat file was missing
    `audio-separator`/`funasr`, the tiered split was missing
    `cryptography` (needed for the mangaz.com adapter's page
    decryption). `requirements.txt` is now a thin `-r` pointer over the
    three tiered files instead of its own package list, so it can't
    silently drift out of sync with them again."""

    def _combined_tier_packages(self):
        return (_requirements_package_names("requirements-core.txt")
                | _requirements_package_names("requirements-media.txt")
                | _requirements_package_names("requirements-optional.txt"))

    def test_cryptography_is_in_the_tiered_split(self):
        assert "cryptography" in self._combined_tier_packages()

    def test_audio_separator_and_funasr_are_in_the_tiered_split(self):
        combined = self._combined_tier_packages()
        assert "audio-separator" in combined
        assert "funasr" in combined

    def test_requirements_txt_is_a_thin_pointer_not_a_fifth_package_list(self):
        """requirements.txt must only ever point at the three tiered
        files, never list a package directly -- a direct package line
        here is exactly how the original drift happened, and nothing
        stops it happening again except this check."""
        path = os.path.join(PROJECT_ROOT, "requirements.txt")
        lines = [l.split("#", 1)[0].strip() for l in open(path, encoding="utf-8")]
        lines = [l for l in lines if l]
        assert lines, "requirements.txt is empty"
        assert all(l.startswith("-r ") for l in lines), (
            f"requirements.txt has a direct package line, not just -r pointers: {lines}")
        pointed_at = {l.split()[1] for l in lines}
        assert pointed_at == {
            "requirements-core.txt", "requirements-media.txt", "requirements-optional.txt"}

    def test_no_script_or_doc_installs_the_old_flat_file_directly(self):
        """Nothing outside requirements.txt's own -r pointers (which this
        test doesn't scan) should invoke `pip install -r requirements.txt`
        directly -- README and start.bat/Diagnostics should all go
        through the tiered files (or requirements.txt's own combined
        pointer) so a reader/script can't reintroduce a fifth,
        independently-drifting install path."""
        pattern = re.compile(r"pip install[^\n]*-r\s+requirements\.txt")
        offenders = []
        for root, dirs, files in os.walk(PROJECT_ROOT):
            dirs[:] = [d for d in dirs if d not in (
                ".git", "venv", "library", "__pycache__", "node_modules")]
            for fname in files:
                if not fname.endswith((".md", ".bat", ".ps1", ".sh")):
                    continue
                path = os.path.join(root, fname)
                text = open(path, encoding="utf-8", errors="ignore").read()
                if pattern.search(text):
                    offenders.append(os.path.relpath(path, PROJECT_ROOT))
        assert offenders == [], (
            f"still installs the flat requirements.txt directly, bypassing the "
            f"tiered split: {offenders}")


class TestRequirementsPackageNameParserItself:
    def test_strips_version_specifiers_and_comments(self, tmp_path):
        p = tmp_path / "req.txt"
        p.write_text("requests>=2.32   # a comment\nurllib3>=2.6\n-r other.txt\n")
        names = set()
        for line in open(p, encoding="utf-8"):
            line = line.split("#", 1)[0].strip()
            if not line or line.startswith("-r"):
                continue
            name = re.split(r"[<>=!~\[\s]", line, 1)[0].strip()
            if name:
                names.add(name.lower())
        assert names == {"requests", "urllib3"}


def _absolute_imports(path):
    """(line, module) for every absolute import in `path`; `from x import y`
    also yields `x.y`, so `from db import foo` and `import db` both show."""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.lineno, node.module
            for alias in node.names:
                yield node.lineno, f"{node.module}.{alias.name}"


def _imports_of(dirname, forbidden):
    offenders = []
    for path in _py_files_under(dirname):
        for line, module in _absolute_imports(path):
            if module == forbidden or module.startswith(forbidden + "."):
                offenders.append(f"{os.path.relpath(path, PROJECT_ROOT)}:{line} {module}")
    return offenders


def _repo_modules():
    """Top-level names that resolve to this repo's own code: every root
    .py stem and every package folder with an __init__.py."""
    names = set()
    for entry in os.listdir(PROJECT_ROOT):
        if entry.endswith(".py"):
            names.add(entry[:-3])
        elif os.path.isfile(os.path.join(PROJECT_ROOT, entry, "__init__.py")):
            names.add(entry)
    return names


class TestLayering:
    """The layers only call downward (CLAUDE.md): services stay UI- and
    HTTP-free so the CLI and the API share them, and routers reach the
    database only through a service, where ownership and whitelists live.
    `lib/` sits below every layer, so any module may import it; in return
    it imports nothing of the app's own code."""

    def test_services_do_not_import_the_api(self):
        assert _imports_of("services", "api") == []

    def test_lib_imports_no_app_module(self):
        forbidden = _repo_modules() - {"lib"}
        offenders = [f"{os.path.relpath(p, PROJECT_ROOT)}:{line} {module}"
                     for p in _py_files_under("lib")
                     for line, module in _absolute_imports(p)
                     if module.split(".")[0] in forbidden]
        assert "services" in forbidden and "db" in forbidden and "core" in forbidden
        assert offenders == [], (
            "lib/ holds helpers with no domain knowledge; move the dependency "
            f"out or keep the module in services/: {offenders}")

    def test_routers_do_not_import_db(self):
        assert _imports_of(os.path.join("api", "routers"), "db") == []

    def test_checker_sees_both_import_forms(self, tmp_path):
        p = tmp_path / "m.py"
        p.write_text("import db\nfrom db import save_lines\nfrom api.auth import x\n"
                     "from . import db as local\n")
        found = [m for _l, m in _absolute_imports(str(p))]
        assert "db" in found and "db.save_lines" in found and "api.auth" in found
        assert "local" not in found and not any(m.startswith(".") for m in found)


def _find_ffmpeg_runs_missing_timeout(source):
    """Line numbers of subprocess.run/check_call/check_output calls that run
    an ffmpeg/ffprobe argument list (a literal, a local variable assigned
    from one, or a *_cmd(...) builder) without timeout=. Popen is left out:
    live capture is a long-lived stream stopped by stop_capture, and the
    cancellable job paths go through background_jobs.run_cancellable."""
    def is_ffmpeg_list(n):
        return (isinstance(n, (ast.List, ast.Tuple)) and n.elts
                and isinstance(n.elts[0], ast.Constant) and n.elts[0].value in ("ffmpeg", "ffprobe"))

    tree = ast.parse(source)
    problems = set()
    for scope in ast.walk(tree):
        if not isinstance(scope, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        names = {t.id for a in ast.walk(scope) if isinstance(a, ast.Assign) and is_ffmpeg_list(a.value)
                 for t in a.targets if isinstance(t, ast.Name)}
        for call in ast.walk(scope):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and call.func.attr in ("run", "check_call", "check_output")
                    and isinstance(call.func.value, ast.Name) and call.func.value.id == "subprocess"
                    and call.args):
                continue
            arg = call.args[0]
            builder = isinstance(arg, ast.Call) and getattr(
                arg.func, "id", getattr(arg.func, "attr", "")).endswith("_cmd")
            if (is_ffmpeg_list(arg) or builder or (isinstance(arg, ast.Name) and arg.id in names)) \
                    and not any(kw.arg == "timeout" for kw in call.keywords):
                problems.add(call.lineno)
    return sorted(problems)


class TestFfmpegRunsHaveTimeouts:
    def test_production_ffmpeg_runs_have_a_timeout(self):
        skip = {"tests", "frontend", "node_modules", ".claude", ".git", "venv", ".venv", "__pycache__"}
        problems = {}
        for root, dirs, files in os.walk(PROJECT_ROOT):
            dirs[:] = [d for d in dirs if d not in skip]
            for name in files:
                if name.endswith(".py"):
                    path = os.path.join(root, name)
                    with open(path, encoding="utf-8") as f:
                        found = _find_ffmpeg_runs_missing_timeout(f.read())
                    if found:
                        problems[os.path.relpath(path, PROJECT_ROOT)] = found
        assert problems == {}, f"ffmpeg subprocess.run without timeout=: {problems}"

    def test_checker_catches_a_missing_timeout(self):
        bad = "import subprocess\ndef f():\n    cmd = ['ffmpeg', '-i', 'a']\n    subprocess.run(cmd, check=True)\n"
        good = bad.replace("check=True", "check=True, timeout=5")
        assert _find_ffmpeg_runs_missing_timeout(bad) == [4]
        assert _find_ffmpeg_runs_missing_timeout(good) == []


def _find_text_captures_missing_decoding(source):
    """Line numbers of subprocess.run/Popen/check_output calls that pass
    text=True (or universal_newlines=True) with neither encoding= nor
    errors=. On Windows that decodes with the locale code page, and a bad
    byte raises inside subprocess's reader thread, so communicate() returns
    stdout=None and the caller fails far from the cause. errors= alone is
    accepted for child Python processes, which write in the locale code page
    themselves."""
    problems = []
    for call in ast.walk(ast.parse(source)):
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr in ("run", "Popen", "check_output")
                and isinstance(call.func.value, ast.Name) and call.func.value.id == "subprocess"):
            continue
        kws = {kw.arg: kw.value for kw in call.keywords}
        textual = any(isinstance(kws.get(k), ast.Constant) and kws[k].value is True
                      for k in ("text", "universal_newlines"))
        if textual and "encoding" not in kws and "errors" not in kws:
            problems.append(call.lineno)
    return problems


class TestSubprocessTextDecoding:
    def test_no_text_capture_without_explicit_decoding(self):
        skip = {"tests", "frontend", "node_modules", ".claude", ".git", "venv", ".venv", "__pycache__"}
        problems = {}
        for root, dirs, files in os.walk(PROJECT_ROOT):
            dirs[:] = [d for d in dirs if d not in skip]
            for name in files:
                if name.endswith(".py"):
                    path = os.path.join(root, name)
                    found = _find_text_captures_missing_decoding(open(path, encoding="utf-8").read())
                    if found:
                        problems[os.path.relpath(path, PROJECT_ROOT)] = found
        assert problems == {}, f"subprocess text=True without encoding=/errors=: {problems}"

    def test_checker_catches_a_missing_encoding(self):
        bad = "import subprocess\nsubprocess.run(['x'], capture_output=True, text=True)\n"
        assert _find_text_captures_missing_decoding(bad) == [2]
        for fix in ("encoding='utf-8'", "errors='replace'"):
            assert _find_text_captures_missing_decoding(bad.replace("text=True", f"text=True, {fix}")) == []
        assert _find_text_captures_missing_decoding(bad.replace("text=True", "check=True")) == []


# Smaller files are easier for a small-context model (and a reviewer) to hold
# in one read. Each entry is the file's size in bytes today; remove an entry
# when the split of that file lands. A listed file may shrink but never grow.
MAX_MODULE_BYTES = 40 * 1024
OVERSIZED_MODULE_BYTES = {
    "db.py": 299105,
    "diagnostics.py": 109274,
    "services/transcribe_service.py": 102565,
    "cli.py": 90858,
    "scanlate.py": 89904,
    "background_jobs.py": 89431,
    "bulk_translate.py": 86382,
    "installer/service.py": 83445,
    "core.py": 84133,
    "services/auto_backup_service.py": 83022,
    "services/disk_usage_service.py": 73715,
    "services/workspace_job_service.py": 65747,
    "services/maintenance_assistant_service.py": 61272,
    "services/library_admin_service.py": 55556,
    "sources/http.py": 49816,
    "services/restructure_service.py": 52468,
    "sources/ai_extract.py": 50796,
    "services/translate_run_service.py": 45837,
    "services/diagnostics_gaps_service.py": 44615,
    "page_fetch.py": 44114,
}


class TestModuleSize:
    @staticmethod
    def _module_sizes():
        skip = {"tests", "frontend", "node_modules", ".claude", ".git", "venv", ".venv", "__pycache__"}
        sizes = {}
        for root, dirs, files in os.walk(PROJECT_ROOT):
            dirs[:] = [d for d in dirs if d not in skip]
            for name in files:
                if name.endswith(".py") and not name.startswith("test_"):
                    path = os.path.join(root, name)
                    sizes[os.path.relpath(path, PROJECT_ROOT).replace(os.sep, "/")] = os.path.getsize(path)
        return sizes

    def test_no_module_over_the_limit_unless_allowlisted(self):
        too_big = {p: s for p, s in self._module_sizes().items()
                   if s > MAX_MODULE_BYTES and p not in OVERSIZED_MODULE_BYTES}
        assert too_big == {}, (
            f"Python modules over {MAX_MODULE_BYTES} bytes: {too_big}. Split the module; "
            "do not add it to OVERSIZED_MODULE_BYTES. New code that would push a file over the limit "
            "goes in a new module that owns one domain; see 'Splitting files' in AGENTS.md.")

    def test_allowlisted_modules_never_grow(self):
        sizes = self._module_sizes()
        grown = {p: (limit, sizes[p]) for p, limit in OVERSIZED_MODULE_BYTES.items()
                 if sizes.get(p, 0) > limit}
        assert grown == {}, (
            f"allowlisted modules grew past their recorded size (limit, now): {grown}. "
            "Undo the growth: put the new code in a new module instead of enlarging these files, "
            "and do not raise the number in OVERSIZED_MODULE_BYTES.")

    def test_stale_allowlist_entries_are_reported_not_failed(self):
        # A split PR must not need to edit the allowlist, so stale entries
        # only warn; the final ratchet PR removes them.
        import warnings
        sizes = self._module_sizes()
        stale = sorted(p for p in OVERSIZED_MODULE_BYTES
                       if p not in sizes or sizes[p] <= MAX_MODULE_BYTES)
        if stale:
            warnings.warn(f"remove from OVERSIZED_MODULE_BYTES (split or deleted): {stale}")


# ---------------------------------------------------------------------------
# Capturing subprocess calls go through lib.proc.
# ---------------------------------------------------------------------------

# subprocess.run(capture_output=True) / Popen(stdout=PIPE) kill only the
# child on a timeout, and on Windows wait for the pipe again with no limit,
# so a grandchild that holds it can hang the caller. Anything long-running
# whose output is read goes through lib.proc (own process group, tree
# kill on timeout or cancel, bounded drain). Dev tooling is not scanned.
# Retired when there are zero call sites outside lib/proc.py.
_CAPTURE_SCAN_SKIP = {"tests", "frontend", "node_modules", ".claude", ".git", "venv", ".venv",
                      "__pycache__", "installer", "scripts", "tools"}
_SUBPROCESS_CALLS = {"run", "Popen", "check_output", "check_call", "call"}

# "path::function" -> why it may capture directly. Only short probes belong
# here; a long-running command uses lib.proc. The "pending" entries
# are known gaps with a timeout= but no tree kill; the list only shrinks
# (a stale entry fails the test below).
_CAPTURE_ALLOWED = {
    # Version and hardware probes, seconds long.
    "diagnostics.py::check_ffmpeg": "probe: ffmpeg -version",
    "diagnostics.py::_warn_deno_old": "probe: deno --version",
    "diagnostics_torch.py::external_gpu_load": "probe: nvidia-smi",
    "diagnostics_torch.py::nvidia_driver_info": "probe: nvidia-smi",
    "services/loaded_models_service.py::_gpu_from_nvidia_smi": "probe: nvidia-smi",
    "media_inspect.py::run_ffprobe": "probe: ffprobe",
    "raw_transcript.py::_git_commit": "probe: git rev-parse",
    "services/bug_report_service.py::_git_commit": "probe: git rev-parse",
    "services/line_provenance_service.py::software_version": "probe: git describe",
    "services/maintenance_assistant_service.py::_git": "probe: read-only git",
    "services/maintenance_assistant_service.py::_tracked_files": "probe: git ls-files",
    "lib/proc_kill.py::kill_tree": "taskkill, 10 s",
    # pending: ffmpeg runs bounded by timeout= only.
    "core.py::extract_audio_from_video": "pending: ffmpeg",
    "core.py::extract_audio_slice": "pending: ffmpeg",
    "dub.py::time_stretch": "pending: ffmpeg",
    "dub_narration.py::export_narration_m4b": "pending: ffmpeg",
    "hardsub_ocr.py::extract_frames": "pending: ffmpeg",
    "services/media_peaks_service.py::_decode": "pending: ffmpeg",
    "services/speech_coverage_service.py::_decode_chunk": "pending: ffmpeg",
    "video_export.py::render_vertical_clip": "pending: ffmpeg",
    "video_export.py::render_preview_clip": "pending: ffmpeg",
    "video_export.py::burn_subtitles": "pending: ffmpeg",
    "video_export.py::burn_ass": "pending: ffmpeg",
    "video_export.py::mux_soft_subtitles": "pending: ffmpeg",
    "video_export.py::replace_audio_with_dub": "pending: ffmpeg",
    # pending: separate tree-killing runners that should move onto lib.proc (see B8 in docs/local-agent-backlog.md).
    "background_jobs.py::run_cancellable": "pending: own runner",
    "services/lncrawl_service.py::_run_process": "pending: own runner",
}


_ALWAYS_CAPTURING = {"check_output", "getoutput", "getstatusoutput"}


# Allowed functions with more than one capturing call (default 1).
_CAPTURE_MAX_CALLS = {}


def _count_capturing_subprocess_calls(source):
    """{function name or "<module>": number of calls} for the functions (or "<module>") with a subprocess call that
    capture output: capture_output=True, stdout=/stderr= PIPE (also -1, or
    PIPE imported by name), or check_output/getoutput/getstatusoutput. Resolves
    `import subprocess as sp` and `from subprocess import run [as r]`."""
    tree = ast.parse(source)
    modules, funcs, pipes = set(), {}, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {a.asname or a.name for a in node.names if a.name == "subprocess"}
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for a in node.names:
                if a.name in _SUBPROCESS_CALLS | _ALWAYS_CAPTURING:
                    funcs[a.asname or a.name] = a.name
                elif a.name == "PIPE":
                    pipes.add(a.asname or a.name)

    def is_pipe(value):
        if isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.USub):
            return isinstance(value.operand, ast.Constant) and value.operand.value == 1
        if isinstance(value, ast.Name):
            return value.id in pipes
        return (isinstance(value, ast.Attribute) and value.attr == "PIPE"
                and isinstance(value.value, ast.Name) and value.value.id in modules)

    def called(call):
        f = call.func
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in modules:
            return f.attr
        if isinstance(f, ast.Name):
            return funcs.get(f.id)
        return None

    def captures(name, call):
        if name in _ALWAYS_CAPTURING:
            return True
        for kw in call.keywords:
            if kw.arg == "capture_output" and not (
                    isinstance(kw.value, ast.Constant) and kw.value.value is False):
                return True
            if kw.arg in ("stdout", "stderr") and is_pipe(kw.value):
                return True
        return False

    found = collections.Counter()

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.stack = []

        def visit_FunctionDef(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, call):
            name = called(call)
            if name in _SUBPROCESS_CALLS | _ALWAYS_CAPTURING and captures(name, call):
                found[self.stack[-1] if self.stack else "<module>"] += 1
            self.generic_visit(call)

    Visitor().visit(tree)
    return found


def _find_capturing_subprocess_calls(source):
    return set(_count_capturing_subprocess_calls(source))


class TestCapturingSubprocessCallsUseTheRunner:
    @staticmethod
    def _scan():
        found = collections.Counter()
        for root, dirs, files in os.walk(PROJECT_ROOT):
            dirs[:] = [d for d in dirs if d not in _CAPTURE_SCAN_SKIP]
            for name in files:
                rel = os.path.relpath(os.path.join(root, name), PROJECT_ROOT).replace(os.sep, "/")
                if not name.endswith(".py") or name.startswith("test_") or rel == "lib/proc.py":
                    continue
                with open(os.path.join(root, name), encoding="utf-8") as f:
                    for fn, n in _count_capturing_subprocess_calls(f.read()).items():
                        found[f"{rel}::{fn}"] = n
        return found

    def test_only_allow_listed_probes_capture_output_directly(self):
        # Counted per function so a second capture inside an allowed one is caught.
        new = sorted(k for k, n in self._scan().items()
                     if k not in _CAPTURE_ALLOWED or n > _CAPTURE_MAX_CALLS.get(k, 1))
        assert new == [], (
            f"subprocess call capturing output outside lib.proc: {new}. A command that can run "
            "for more than a few seconds uses lib.proc.run_captured / stream_tree "
            "(tree kill on timeout and cancel, bounded drain); a short probe is added to "
            "_CAPTURE_ALLOWED with its reason.")

    def test_allow_list_has_no_stale_entries(self):
        stale = sorted(set(_CAPTURE_ALLOWED) - set(self._scan()))
        assert stale == [], f"remove from _CAPTURE_ALLOWED (moved to the runner or deleted): {stale}"

    def test_checker_finds_each_capturing_form(self):
        src = ("import subprocess\nimport subprocess as sp\n"
               "from subprocess import run, PIPE, Popen as P\n"
               "def a():\n    subprocess.run(['x'], capture_output=True)\n"
               "def b():\n    subprocess.Popen(['x'], stdout=subprocess.PIPE)\n"
               "def c():\n    subprocess.check_output(['x'], timeout=5)\n"
               "def d():\n    subprocess.run(['x'], capture_output=False)\n"
               "def e():\n    subprocess.run(['x'], stdout=subprocess.DEVNULL)\n"
               "def f():\n    run(['x'], stdout=PIPE)\n"
               "def g():\n    sp.run(['x'], stderr=sp.PIPE)\n"
               "def h():\n    P(['x'], stdout=-1)\n"
               "def i():\n    subprocess.getoutput('x')\n"
               "def j():\n    run(['x'])\n"
               "subprocess.run(['x'], capture_output=True)\n")
        assert _find_capturing_subprocess_calls(src) == {
            "a", "b", "c", "f", "g", "h", "i", "<module>"}
