"""tests/test_source_status.py -- the generated status board in
docs/known-working-sources.md, its data file, and the reachability probe
(scripts/source_status.py, scripts/source_probe.py). No network."""

import datetime
import importlib.util
import os

import pytest
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "scripts", f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ss = _load("source_status")
sp = _load("source_probe")


def test_doc_matches_generated_output():
    with open(ss.DOC_PATH, encoding="utf-8") as f:
        assert f.read() == ss.render(), (
            "docs/known-working-sources.md is out of date: add or edit the row in "
            "docs/source-status.json, then run `python scripts/source_status.py`")


def test_data_rows_are_valid():
    data = ss.load_data()
    assert ss.data_problems(data) == []
    for group in ("registered", "generic", "set_aside"):
        for key, row in data[group].items():
            assert row["status"] in ss.STATUSES, (group, key)
            if row["last_verified"]:
                datetime.date.fromisoformat(row["last_verified"])


def test_every_registered_adapter_is_listed():
    block = ss.build_block(ss.load_data(), ss.registered_adapters())
    for name in ss.registered_adapters():
        assert f"| `{name}` |" in block
    assert "demo" not in ss.registered_adapters()


def test_missing_row_shows_no_status_recorded_and_orphan_is_named():
    data = ss.load_data()
    data["registered"].pop("mangaz")
    data["registered"]["gone"] = {"status": "confirmed_live", "last_verified": ""}
    block = ss.build_block(data, ss.registered_adapters())
    mangaz = next(l for l in block.splitlines() if "| `mangaz` |" in l)
    assert ss.NO_STATUS in mangaz
    assert "`gone`" in block.split("## Generic")[0]


def test_every_data_row_names_a_real_adapter():
    assert set(ss.load_data()["registered"]) <= set(ss.registered_adapters())


def test_hosts_from_pattern():
    assert ss.hosts_from_pattern(r"(?:^|//|\.)(?:manhuagui|mhgui)\.com/comic/\d+") == \
        ["manhuagui.com", "mhgui.com"]
    assert ss.hosts_from_pattern(r"b23\.tv/") == ["b23.tv"]
    assert ss.hosts_from_pattern(r"toonkor\d*\.(?:org|com|net)") == []


# --- probe ---------------------------------------------------------------

def _fetcher(script):
    calls = []

    def fetch(url):
        calls.append(url)
        out = script[url]
        if isinstance(out, Exception):
            raise out
        return out
    fetch.calls = calls
    return fetch


def test_probe_classifies_each_outcome():
    ok = _fetcher({"https://a.example/": (200, {}, "<html><title>hi</title>" + "x" * 3000 + "</html>")})
    assert sp.probe_host("a.example", ok)["classification"] == "reachable"
    cf = _fetcher({"https://b.example/": (403, {"cf-mitigated": "challenge"}, "Just a moment")})
    assert sp.probe_host("b.example", cf)["classification"] == "challenge-page"
    err = _fetcher({"https://c.example/": (503, {}, "down")})
    assert sp.probe_host("c.example", err)["classification"] == "http-error"
    dns = _fetcher({"https://d.example/": requests.ConnectionError("NameResolutionError: getaddrinfo failed")})
    assert sp.probe_host("d.example", dns)["classification"] == "dns-failure"
    slow = _fetcher({"https://e.example/": requests.ConnectTimeout("t")})
    assert sp.probe_host("e.example", slow)["classification"] == "timeout"
    refused = _fetcher({"https://f.example/": requests.ConnectionError("Connection refused")})
    assert sp.probe_host("f.example", refused)["classification"] == "connection-error"


def test_probe_follows_at_most_three_redirects():
    hop = lambda n: (302, {"Location": f"/{n}"}, "")
    three = _fetcher({"https://r.example/": hop(1), "https://r.example/1": hop(2),
                      "https://r.example/2": hop(3), "https://r.example/3": (200, {}, "ok")})
    assert sp.probe_host("r.example", three)["classification"] == "reachable"
    four = _fetcher({"https://r.example/": hop(1), "https://r.example/1": hop(2),
                     "https://r.example/2": hop(3), "https://r.example/3": hop(4)})
    res = sp.probe_host("r.example", four)
    assert res["classification"] == "http-error" and len(four.calls) == 4


def test_http_fetch_uses_timeout_no_redirects_no_proxy(monkeypatch):
    seen = {}

    class Resp:
        status_code, headers, encoding = 200, {}, "utf-8"
        def iter_content(self, n): yield b"ok"
        def close(self): pass

    class Sess:
        def __init__(self): self.proxies = None
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def request(self, method, url, **kw):
            seen.update(kw, method=method, url=url, trust_env=self.trust_env, proxies=self.proxies)
            return Resp()

    monkeypatch.setattr(requests, "Session", Sess)
    assert sp.http_fetch("https://x.example/") == (200, {}, "ok")
    assert seen["timeout"] == (5, 10) and seen["allow_redirects"] is False
    assert seen["trust_env"] is False and seen["proxies"] == {}
    assert "Baihe" in seen["headers"]["User-Agent"]


def test_hosts_and_set_aside_reporting():
    data = ss.load_data()
    default = {h["host"] for h in sp.collect_hosts(False, data)}
    everything = {h["host"]: h for h in sp.collect_hosts(True, data)}
    assert "dl-raw.si" not in default and everything["dl-raw.si"]["set_aside"]
    assert "www.xbanxia.cc" in default and "m.zgzl.net" in default

    entries = [{"host": "up.example", "origin": "x", "set_aside": True},
               {"host": "down.example", "origin": "y", "set_aside": True},
               {"host": "live.example", "origin": "z", "set_aside": False}]
    fetch = _fetcher({"https://up.example/": (200, {}, "hello"),
                      "https://down.example/": requests.ConnectionError("getaddrinfo failed"),
                      "https://live.example/": (200, {}, "hello")})
    sleeps = []
    results = sp.probe_all(entries, fetch, sleep=sleeps.append)
    assert sleeps == [2.0, 2.0]
    assert sp.answered_again(results) == ["up.example"]
    assert "up.example" in sp.format_table(results)


def test_probe_refuses_in_ci(monkeypatch, capsys):
    monkeypatch.setenv("CI", "true")
    fetch = _fetcher({})
    assert sp.main([], fetch=fetch) == 2
    assert fetch.calls == []
