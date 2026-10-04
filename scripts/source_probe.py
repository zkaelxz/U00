"""
scripts/source_probe.py -- which source hosts still answer? A manual dev tool.

    python scripts/source_probe.py                     # adapter + status-file hosts
    python scripts/source_probe.py --json
    python scripts/source_probe.py --include-set-aside # also re-probe declined/refused hosts

For each host: ONE GET of the site root, sequentially with a 2 s gap, an honest
User-Agent, timeout=(5, 10), at most 3 redirects, no cookies, no proxy. Each
host is classified reachable / challenge-page / http-error / dns-failure /
timeout (connection-error for any other network failure). A challenge page is
only named, never solved. Nothing is written anywhere: with --include-set-aside
it lists the set-aside hosts that answered again and leaves the decision (and
docs/source-status.json) to a person. Refuses to run when CI is set (--allow-ci
overrides); tests use a fake transport.

Hosts come from docs/source-status.json and the adapters' url_patterns; no
network is used to build the list.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

# source_status is the sibling scripts/source_status.py, not a package
# module: put this file's own folder on the path so the import works when
# the script is run from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_status  # noqa: E402

USER_AGENT = "BaiheSubtitler-reachability-probe/1.0 (manual dev check; one GET of the site root)"
TIMEOUT = (5, 10)
MAX_REDIRECTS = 3
GAP_SECONDS = 2.0
MAX_BODY_BYTES = 200_000

REACHABLE, CHALLENGE, HTTP_ERROR = "reachable", "challenge-page", "http-error"
DNS_FAILURE, TIMEOUT_, CONNECTION_ERROR = "dns-failure", "timeout", "connection-error"

_DNS_MARKERS = ("name or service not known", "getaddrinfo", "nameresolutionerror",
                "nodename nor servname", "temporary failure in name resolution",
                "no address associated")


def collect_hosts(include_set_aside: bool = False, data: dict = None) -> list:
    """[{host, origin, set_aside}] in a stable order, from the data file's hosts
    and the adapters' url_patterns (offline)."""
    data = data if data is not None else source_status.load_data()
    found = {}

    def add(host, origin, set_aside=False):
        entry = found.setdefault(host, {"host": host, "origin": origin, "set_aside": set_aside})
        entry["set_aside"] = entry["set_aside"] and set_aside

    for name, cls in source_status.registered_adapters().items():
        for h in source_status.adapter_hosts(cls, data["registered"].get(name)):
            add(h, f"adapter:{name}")
    for key, row in data["generic"].items():
        add(key, "generic")
    for key, row in data["set_aside"].items():
        for h in row.get("hosts") or []:
            add(h, f"set-aside:{key}", set_aside=True)
    hosts = list(found.values())
    return hosts if include_set_aside else [h for h in hosts if not h["set_aside"]]


def http_fetch(url: str):
    """One GET, no redirects followed, no cookies kept, no proxy. Returns
    (status, headers, body_text)."""
    import requests
    with requests.Session() as session:
        session.trust_env = False
        session.proxies = {}
        resp = session.request("GET", url, headers={"User-Agent": USER_AGENT},
                               timeout=TIMEOUT, allow_redirects=False, stream=True)
        try:
            body = next(resp.iter_content(MAX_BODY_BYTES), b"") or b""
            text = body.decode(resp.encoding or "utf-8", errors="replace")
            return resp.status_code, dict(resp.headers), text
        finally:
            resp.close()


def _is_dns_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return any(m in text for m in _DNS_MARKERS)


def _challenge(status, headers, body, url, final_url) -> bool:
    try:
        from sources import detect
        from sources.models import FailureReason
    except Exception:
        return False
    reasons = detect.classify(status, headers, body, url, final_url)
    return any(r in (FailureReason.CLOUDFLARE_CHALLENGE, FailureReason.BOT_CHALLENGE)
               for r in reasons)


def probe_host(host: str, fetch=http_fetch) -> dict:
    """Classify one host. `fetch(url) -> (status, headers, body)` is the only
    network seam; it may raise requests exceptions."""
    import requests
    start = f"https://{host}/"
    url, hops = start, 0
    result = {"host": host, "url": start, "classification": CONNECTION_ERROR,
              "status": None, "final_url": start, "detail": ""}
    while True:
        try:
            status, headers, body = fetch(url)
        except requests.Timeout:
            result.update(classification=TIMEOUT_, detail="timed out")
            return result
        except requests.RequestException as exc:
            dns = _is_dns_error(exc)
            result.update(classification=DNS_FAILURE if dns else CONNECTION_ERROR,
                          detail="name did not resolve" if dns else type(exc).__name__)
            return result
        result.update(status=status, final_url=url)
        location = {str(k).lower(): v for k, v in (headers or {}).items()}.get("location")
        if status in (301, 302, 303, 307, 308) and location:
            if hops >= MAX_REDIRECTS:
                result.update(classification=HTTP_ERROR, detail="more than 3 redirects")
                return result
            hops += 1
            nxt = urljoin(url, location)
            if urlsplit(nxt).scheme not in ("http", "https"):
                result.update(classification=HTTP_ERROR, detail="redirect to a non-http URL")
                return result
            url = nxt
            continue
        if _challenge(status, headers, body, start, url):
            result.update(classification=CHALLENGE, detail="challenge page (not attempted)")
        elif status >= 400:
            result.update(classification=HTTP_ERROR, detail=f"HTTP {status}")
        else:
            result.update(classification=REACHABLE)
        return result


def probe_all(entries, fetch=http_fetch, gap=GAP_SECONDS, sleep=time.sleep) -> list:
    results = []
    for i, entry in enumerate(entries):
        if i:
            sleep(gap)
        r = probe_host(entry["host"], fetch)
        r.update(origin=entry["origin"], set_aside=entry["set_aside"])
        results.append(r)
    return results


def answered_again(results) -> list:
    """Set-aside hosts that gave a real answer this time (a human decides)."""
    return [r["host"] for r in results if r["set_aside"] and r["classification"] == REACHABLE]


def format_table(results) -> str:
    rows = [("host", "result", "status", "origin")] + [
        (r["host"], r["classification"], str(r["status"] or "-"),
         r["origin"] + (" (set aside)" if r["set_aside"] else "")) for r in results]
    widths = [max(len(row[i]) for row in rows) for i in range(4)]
    return "\n".join("  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip() for row in rows)


def main(argv=None, fetch=http_fetch, sleep=time.sleep) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true", help="print JSON instead of a table")
    ap.add_argument("--include-set-aside", action="store_true",
                    help="also re-probe hosts marked set aside and list those that answered")
    ap.add_argument("--allow-ci", action="store_true", help="run even though CI is set")
    args = ap.parse_args(argv)
    if os.environ.get("CI") and not args.allow_ci:
        print("source_probe is a manual tool and does nothing when CI is set "
              "(use --allow-ci to override).", file=sys.stderr)
        return 2
    entries = collect_hosts(args.include_set_aside)
    results = probe_all(entries, fetch, sleep=sleep)
    again = answered_again(results) if args.include_set_aside else []
    if args.json:
        print(json.dumps({"results": results, "set_aside_answered_again": again},
                         ensure_ascii=False, indent=2))
    else:
        print(format_table(results))
        if args.include_set_aside:
            print("\nSet-aside hosts that answered again (no file was changed; a person decides): "
                  + (", ".join(again) if again else "none"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
