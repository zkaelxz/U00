"""
The Caddy template for household access (deploy/caddy/Caddyfile.template).

Parses the template with a small Caddyfile reader and checks what the guide
(docs/household-access.md) promises: Caddy proxies only to the household
listener, sets the forwarding headers itself and drops X-Baihe-*, refuses an
uncleaned path first, and refuses every local_only() route at the proxy (defence
in depth: Baihe refuses them itself), so a new local_only() route that the
template doesn't cover fails here. Path matching follows Caddy's `path`
matcher for the forms the template may use: a trailing `*` alone is a plain
prefix match, a leading `*` alone a suffix match, and `*` inside a pattern
matches within one path segment (Go's path.Match). A pattern mixing those forms
is refused by the test rather than modelled.
"""

import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "deploy" / "caddy" / "Caddyfile.template"
DECISION_DOC = ROOT / "docs" / "remote-access-decision.md"

UPSTREAM = "127.0.0.1:{$BAIHE_API_HOUSEHOLD_PORT}"
SITE = "{$BAIHE_DOMAIN}"
# Household-permission routes the proxy refuses on purpose: bulk translate is
# only reachable from the Library admin page, which the proxy blocks as a whole.
BLOCKED_ON_PURPOSE = {("POST", "/api/library/admin/bulk/translate")}


# --- a minimal Caddyfile reader -------------------------------------------

def _strip_comments(text):
    return "\n".join(re.sub(r"(^|\s)#.*$", "", line) for line in text.splitlines())


def _tokens(line):
    return [m.group(1) if m.group(1) is not None else m.group(2)
            for m in re.finditer(r'"([^"]*)"|(\S+)', line)]


def _parse(text):
    """Nested blocks: each node is {"tokens": [...], "children": [...]}."""
    root = {"tokens": [], "children": []}
    stack = [root]
    for raw in _strip_comments(text).splitlines():
        toks = _tokens(raw)
        if not toks:
            continue
        if toks == ["}"]:
            stack.pop()
            continue
        opens = toks[-1] == "{"
        node = {"tokens": toks[:-1] if opens else toks, "children": []}
        stack[-1]["children"].append(node)
        if opens:
            stack.append(node)
    assert len(stack) == 1, "unbalanced braces in the template"
    return root


def _walk(node):
    for child in node["children"]:
        yield child
        yield from _walk(child)


def _site():
    tree = _parse(TEMPLATE.read_text(encoding="utf-8"))
    # The global options block is the one with no tokens before its brace.
    sites = [n for n in tree["children"] if n["tokens"]]
    assert [n["tokens"] for n in sites] == [[SITE]], sites
    return sites[0]


def _global():
    tree = _parse(TEMPLATE.read_text(encoding="utf-8"))
    (block,) = [n for n in tree["children"] if not n["tokens"]]
    return block


def _directive(node, name):
    return [c for c in node["children"] if c["tokens"][0] == name]


# --- Caddy's path and method matching -------------------------------------

def _check_pattern(pattern):
    """Only the forms whose Caddy semantics the reader models: a leading or
    trailing `*` must be the pattern's only `*`."""
    if pattern.startswith("*") or pattern.endswith("*"):
        assert pattern.count("*") == 1, f"multi-wildcard path pattern {pattern!r}"


def _path_match(pattern, path):
    _check_pattern(pattern)
    p, s = pattern.lower(), path.lower()
    if p.startswith("*"):
        return s.endswith(p[1:])
    if p.endswith("*"):
        return s.startswith(p[:-1])
    return re.fullmatch("".join("[^/]*" if ch == "*" else re.escape(ch) for ch in p), s) is not None


def _conditions(block):
    """(methods or None, paths or None, [negated sub-conditions]) of a matcher block."""
    methods, paths, nots = None, None, []
    for c in block["children"]:
        kind, args = c["tokens"][0], c["tokens"][1:]
        if kind == "method":
            methods = (methods or set()) | {a.upper() for a in args}
        elif kind == "path":
            for a in args:
                _check_pattern(a)
            paths = (paths or []) + args
        elif kind == "not":
            assert not args, "use the block form of `not` in PC-only matchers"
            nots.append(_conditions(c))
        else:
            raise AssertionError(f"unexpected matcher {kind!r} in a PC-only matcher")
    return methods, paths, nots


def _matches(cond, method, path):
    methods, paths, nots = cond
    if methods is not None and method not in methods:
        return False
    if paths is not None and not any(_path_match(p, path) for p in paths):
        return False
    return not any(_matches(n, method, path) for n in nots)


UNCLEAN = "@unclean_path"


def _unclean_regex():
    """The regexp of the uncleaned-path matcher (RE2 syntax; the parts used
    here mean the same in Python's re)."""
    (m,) = [c for c in _site()["children"] if c["tokens"][0] == UNCLEAN]
    assert m["tokens"][1:3] == ["vars_regexp", "{http.request.uri.path}"] and not m["children"]
    return re.compile(m["tokens"][3])


def _blocking_matchers():
    """Matchers of every `respond @x 4xx` placed before the proxy in the
    route, except the uncleaned-path one (tested on its own)."""
    site = _site()
    named = {c["tokens"][0]: c for c in site["children"] if c["tokens"][0].startswith("@")}
    (route,) = _directive(site, "route")
    order = [c["tokens"][0] for c in route["children"]]
    assert order.index("reverse_proxy") > max(i for i, d in enumerate(order) if d == "respond")
    out = []
    for c in _directive(route, "respond"):
        name, status = c["tokens"][1], c["tokens"][2]
        if name == UNCLEAN:
            continue
        assert status in ("403", "404")
        block = named[name]
        assert len(block["tokens"]) == 1 and block["children"], f"{name} must be a matcher block"
        out.append(_conditions(block))
    return out


def _blocked(method, path, matchers):
    return any(_matches(m, method, path) for m in matchers)


def _sample(path):
    return re.sub(r"\{[^}]+\}", "7", path)


# --- route tables ---------------------------------------------------------

@pytest.fixture(scope="module")
def routes():
    app = create_app(ApiSettings(household_port=8610), listener="household")
    return [(path, methods, decls) for _r, path, methods, decls
            in api_auth.iter_route_declarations(app)]


def _doc_local_only():
    for line in DECISION_DOC.read_text(encoding="utf-8").splitlines():
        if line.startswith("| local_only() |"):
            return re.findall(r"`([A-Z]+) (/[^`]+)`", line)
    raise AssertionError("no local_only() row in the route table")


# --- tests ----------------------------------------------------------------

def test_proxies_only_to_the_household_port():
    site = _site()
    proxies = [n for n in _walk(site) if n["tokens"][0] == "reverse_proxy"]
    assert [p["tokens"][1:] for p in proxies] == [[UPSTREAM]]


def test_no_admin_or_bridge_port_and_no_literal_port_anywhere():
    text = _strip_comments(TEMPLATE.read_text(encoding="utf-8"))
    for port in ("8600", "8601", "8610", "8756", "8501"):
        assert port not in text
    assert not re.search(r":\d", text), "ports come from placeholders only"


def test_no_real_domain_address_or_secret():
    text = TEMPLATE.read_text(encoding="utf-8")
    for name in re.findall(r"\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b", text, re.I):
        if name.lower().endswith((".md", ".py", ".json", ".template", ".log")):
            continue
        if name.lower().startswith("http.request."):   # a Caddy placeholder
            continue
        assert name.lower().endswith("example.com") or name.lower() == "github.com", name
    assert set(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text)) == {"127.0.0.1"}
    assert not re.search(r"@(?!example\.com)[a-z0-9-]+\.[a-z]", text, re.I), "no real e-mail"
    assert not re.search(r"GOCSPX-|AIza|sk-[A-Za-z0-9]|[A-Za-z0-9+_-]{32,}", text)


def test_forwarding_headers_set_by_caddy_and_x_baihe_stripped():
    (proxy,) = [n for n in _walk(_site()) if n["tokens"][0] == "reverse_proxy"]
    ups = [c["tokens"][1:] for c in proxy["children"] if c["tokens"][0] == "header_up"]
    assert ["X-Forwarded-For", "{remote_host}"] in ups
    assert ["X-Forwarded-Proto", "{scheme}"] in ups
    assert ["X-Forwarded-Host", "{host}"] in ups
    # Go canonicalises header names, and Caddy's wildcard delete compares
    # against the canonical form, so the case here matters.
    assert ["-X-Baihe-*"] in ups
    assert ["-Forwarded"] in ups and ["-X-Real-Ip"] in ups


@pytest.mark.parametrize("path", [
    "/api/system/shutdown", "/api/settings", "/api/settings/keys/openai",
    "/api/library/admin/storage", "/api/diagnostics", "/api/diagnostics/log",
    "/api/admin/users/1/deactivate", "/api/admin/users/1", "/api/admin/other",
    "/api/docs", "/api/openapi.json"])
def test_admin_paths_refused_for_any_method(path):
    matchers = _blocking_matchers()
    for method in ("GET", "HEAD", "POST", "DELETE", "PUT", "PATCH"):
        assert _blocked(method, path, matchers), f"{method} {path}"


ADMIN_VIEWS = [("GET", "/api/admin/users"), ("GET", "/api/admin/audit")]


def test_only_the_admin_views_pass_and_only_for_reads(routes):
    """Admin view routes (admin.users.read) pass so an admin can see the
    users and the audit log from away; every other method on them, and
    every admin write route, is refused."""
    matchers = _blocking_matchers()
    views = {(m, p) for p, methods, decls in routes for m in methods
             if decls and decls[0][0] == "permission"
             and decls[0][1] in auth_service.ADMIN_VIEW_PERMISSIONS}
    assert views == set(ADMIN_VIEWS)
    for _m, path in ADMIN_VIEWS:
        for method in ("GET", "HEAD"):
            assert not _blocked(method, path, matchers), f"{method} {path}"
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            assert _blocked(method, path, matchers), f"{method} {path}"
    writes = sorted(f"{m} {p}" for p, methods, decls in routes for m in methods
                    if p.startswith("/api/admin/") and (m, p) not in ADMIN_VIEWS
                    and not _blocked(m, _sample(p), matchers))
    assert not writes, writes


def test_every_local_only_route_is_refused(routes):
    matchers = _blocking_matchers()
    local = {(m, p) for p, methods, decls in routes if decls == [("local_only", None)]
             for m in methods}
    assert local, "the app has no local_only() routes?"
    documented = set(_doc_local_only())
    assert documented, "no local_only() routes in the decision doc's table"
    missing = sorted(f"{m} {p}" for m, p in local | documented
                     if not _blocked(m, _sample(p), matchers)
                     or (m == "GET" and not _blocked("HEAD", _sample(p), matchers)))
    assert not missing, (
        "local_only() routes the Caddy template does not refuse; add them to a "
        "PC-only matcher in deploy/caddy/Caddyfile.template:\n  " + "\n  ".join(missing))


def test_household_routes_are_not_refused(routes):
    matchers = _blocking_matchers()
    wrong = []
    for path, methods, decls in routes:
        if len(decls) != 1 or decls[0][0] == "local_only":
            continue
        kind, perm = decls[0]
        if (kind == "permission" and perm.startswith("admin.")
                and perm not in auth_service.ADMIN_VIEW_PERMISSIONS):
            continue
        for m in methods:
            if (m, path) not in BLOCKED_ON_PURPOSE and _blocked(m, _sample(path), matchers):
                wrong.append(f"{m} {path} ({perm or kind})")
    assert not wrong, "household routes the template refuses:\n  " + "\n  ".join(wrong)
    for method, path in [("GET", "/"), ("GET", "/assets/app.js"), ("GET", "/api/health"),
                         ("GET", "/api/auth/login"), ("POST", "/api/diagnostics/bug-reports")]:
        assert not _blocked(method, path, matchers), f"{method} {path}"


def test_blocked_on_purpose_routes_still_exist_with_a_household_permission(routes):
    table = {(m, p): decls for p, methods, decls in routes for m in methods}
    for key in BLOCKED_ON_PURPOSE:
        assert key in table, key
        assert table[key][0][0] == "permission" and not table[key][0][1].startswith("admin.")


def test_path_matcher_model_follows_caddy():
    assert _path_match("/api/dramas/*/cover", "/api/dramas/7/cover")
    assert not _path_match("/api/dramas/*/cover", "/api/dramas/7/x/cover")
    assert _path_match("/api/settings/*", "/api/settings/keys/x")
    assert not _path_match("/api/settings/*", "/api/settings")
    for mixed in ("/api/a/*/b*", "*/api/*", "*a*"):
        with pytest.raises(AssertionError):
            _path_match(mixed, "/api/a/7/b")


def test_no_multi_wildcard_path_patterns():
    site = _site()
    patterns = [a for n in _walk(site) if n["tokens"][0] == "path" for a in n["tokens"][1:]]
    assert patterns
    for pattern in patterns:
        _check_pattern(pattern)


BAD_PATHS = ["/api/../api/settings", "/api/./settings", "/api/settings/.", "/api/x/..",
             "/api//settings", "//api/settings", "/api/%2e%2e/settings", "/api/%2E/settings",
             "/api/.%2e/x", "/api%2f%2fsettings", "/api/%2f/x", "/..", "/."]
GOOD_PATHS = ["/", "/api/health", "/assets/app.js", "/.well-known/x", "/api/a.b/c",
              "/api/..x/y", "/api/x../y", "/api/%2e%2ex"]


def test_unclean_paths_refused_first():
    (route,) = _directive(_site(), "route")
    first = route["children"][0]["tokens"]
    assert first[:2] == ["respond", UNCLEAN] and first[2] == "400"
    rx = _unclean_regex()
    for path in BAD_PATHS:
        assert rx.search(path), path
    for path in GOOD_PATHS:
        assert not rx.search(path), path


def test_unclean_path_matcher_passes_every_route(routes):
    rx = _unclean_regex()
    hit = sorted({p for p, _m, _d in routes if rx.search(_sample(p))})
    assert not hit, hit


def test_caddy_admin_api_off():
    assert ["admin", "off"] in [c["tokens"] for c in _global()["children"]]


def test_site_top_level_directives_pinned():
    """A new top-level directive (another proxy, a file server, a redirect)
    must be reviewed and added here on purpose."""
    tokens = sorted(c["tokens"][0] for c in _site()["children"])
    assert tokens == sorted([
        "request_body", "@compressible", "encode", "header", "header", "header", "header",
        "header", "log", UNCLEAN, "@pc_only", "@pc_only_post", "@pc_only_delete", "route"])
    (route,) = _directive(_site(), "route")
    assert [c["tokens"][0] for c in route["children"]] == [
        "respond", "respond", "respond", "respond", "rate_limit", "reverse_proxy"]


def test_sign_in_is_rate_limited():
    (route,) = _directive(_site(), "route")
    (limit,) = _directive(route, "rate_limit")
    zones = [z for z in limit["children"] if z["tokens"][0] == "zone"]
    paths = {p for z in zones for n in _walk(z) if n["tokens"][0] == "path"
             for p in n["tokens"][1:]}
    assert {"/api/auth/login", "/api/auth/callback", "/api/auth/handoff"} <= paths
    for z in zones:
        assert _directive(z, "key")[0]["tokens"][1] == "{remote_host}"


def _filter_rules(log):
    (fmt,) = [n for n in _walk(log) if n["tokens"][:2] == ["format", "filter"]]
    return [c["tokens"] for c in fmt["children"]]


def test_access_log_drops_query_strings_and_credentials():
    (log,) = _directive(_site(), "log")
    rules = _filter_rules(log)
    assert ["request>uri", "regexp", "[?].*", ""] in rules
    for field in ("request>headers>Cookie", "request>headers>X-Csrf-Token",
                  "resp_headers>Set-Cookie", "resp_headers>Location"):
        assert [field, "delete"] in rules


def test_caddy_own_log_drops_query_strings_and_credentials():
    (log,) = [c for c in _global()["children"] if c["tokens"] == ["log", "default"]]
    rules = _filter_rules(log)
    assert ["request>uri", "regexp", "[?].*", ""] in rules
    for field in ("request>headers>Cookie", "request>headers>Authorization",
                  "request>headers>X-Csrf-Token"):
        assert [field, "delete"] in rules


def test_body_size_limited_and_security_headers_set():
    site = _site()
    (body,) = _directive(site, "request_body")
    assert _directive(body, "max_size")[0]["tokens"][1] == "8MB"
    headers = _directive(site, "header")
    names = {c["tokens"][0] for h in headers for c in h["children"]}
    names |= {h["tokens"][1] for h in headers if len(h["tokens"]) > 1}
    assert {"Strict-Transport-Security", "?X-Content-Type-Options", "?X-Frame-Options",
            "?Content-Security-Policy", "?Referrer-Policy", "-Server", "-Via"} <= names
    # Caddy merges the `?` fields of one block into a single condition (set
    # them all only if all are absent), so one app header would cancel the rest.
    for h in headers:
        assert len([c for c in h["children"] if c["tokens"][0].startswith("?")]) <= 1
