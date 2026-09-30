"""
scripts/source_status.py -- builds docs/known-working-sources.md's status board
from the adapter registry plus docs/source-status.json.

    python scripts/source_status.py           # rewrite the generated block
    python scripts/source_status.py --check  # exit 1 if the doc is out of date

Only the text between the BEGIN/END markers is generated; the intro, legend and
anything after the END marker are hand-written and left alone. An adapter with no
row in the data file shows as "no status recorded" -- add its row to
docs/source-status.json. Offline: reads no network and no runtime state (the
Sources tab's health light is a live signal and is not part of this board).
"""

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = REPO_ROOT / "docs" / "source-status.json"
DOC_PATH = REPO_ROOT / "docs" / "known-working-sources.md"

BEGIN = ("<!-- BEGIN GENERATED: scripts/source_status.py "
         "(edit docs/source-status.json and re-run the script; do not edit by hand) -->")
END = "<!-- END GENERATED -->"

STATUSES = {
    "confirmed_live": "✅",
    "caveat": "⚠️",
    "needs_work": "🔧",
    "declined": "⛔",
    "refused": "🚫",
    "unvetted": "❔",
}
NO_STATUS = "❔ no status recorded"
UNVERIFIED = "unverified date"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def load_data(path=DATA_PATH) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    for group in ("registered", "generic", "set_aside"):
        data.setdefault(group, {})
    return data


def data_problems(data: dict) -> list:
    """Every row's status must be a known key and last_verified an ISO date or empty."""
    problems = []
    for group in ("registered", "generic", "set_aside"):
        for key, row in data[group].items():
            if row.get("status") not in STATUSES:
                problems.append(f"{group}/{key}: unknown status {row.get('status')!r}")
            when = row.get("last_verified", "")
            if when:
                try:
                    datetime.date.fromisoformat(when)
                except (TypeError, ValueError):
                    problems.append(f"{group}/{key}: last_verified {when!r} is not an ISO date")
    return problems


def registered_adapters() -> dict:
    """Registered adapters in the BUILTIN list's order. Registration order
    depends on which modules were imported first (a test run differs from a
    plain script run), so it can't be used for a stable board."""
    from sources import registry
    from sources.adapters import BUILTIN
    rank = {name: i for i, name in enumerate(BUILTIN)}
    found = {n: c for n, c in registry.adapter_classes().items()
             if not getattr(c, "is_demo", False)}
    return {n: found[n] for n in sorted(found, key=lambda n: (rank.get(n, len(rank)), n))}


def _expand_groups(s: str) -> list:
    m = re.search(r"\(\?:([^()]*)\)", s)
    if not m:
        return [s]
    out = []
    for alt in m.group(1).split("|"):
        out += _expand_groups(s[:m.start()] + alt + s[m.end():])
    return out


def hosts_from_pattern(pattern: str) -> list:
    """Best-effort literal hosts a url_pattern names: the part before the first
    top-level '/', with (?:a|b) groups expanded. A pattern whose host needs real
    regex (e.g. toonkor\\d*) yields nothing; give that row `hosts` in the data file."""
    s = pattern.replace("(?:^|//|\\.)", "").lstrip("^")
    depth, end = 0, len(s)
    for i, ch in enumerate(s):
        depth += (ch == "(") - (ch == ")")
        if ch == "/" and depth == 0:
            end = i
            break
    out = []
    for h in _expand_groups(s[:end]):
        h = h.replace("\\.", ".")
        if re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", h):
            out.append(h)
    return out


def adapter_hosts(cls, row=None) -> list:
    if row and row.get("hosts"):
        return list(row["hosts"])
    seen = []
    for p in cls.url_patterns:
        for h in hosts_from_pattern(p):
            if h not in seen:
                seen.append(h)
    return seen


def _cell(text) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ").strip()


def _verified(row) -> str:
    return row.get("last_verified") or UNVERIFIED


def _flags(cls) -> str:
    flags = []
    if cls.supports_adult_toggle:
        flags.append("adult toggle")
    if cls.auth_required:
        flags.append("sign-in required")
    elif cls.auth_supported:
        flags.append("sign-in optional")
    if not cls.allow_browser:
        flags.append("no browser")
    if not cls.chapters_in_site_order:
        flags.append("chapters sorted by number")
    return ", ".join(flags)


def _text(row) -> str:
    return " ".join(t for t in (row.get("notes"), row.get("reason")) if t)


def build_block(data: dict, adapters: dict) -> str:
    out = [BEGIN, ""]
    out += ["## Registered adapters (`sources/adapters/*.py`)", "",
            "Site, language, type, hosts and flags come from the adapter itself; status, date "
            "and notes from `docs/source-status.json`. The Sources tab's 🟢/🟡/🔴 health light "
            "is a runtime signal and is not recorded here.", "",
            "| Site | Adapter | Language | Type | Hosts | Flags | Status | Verified | Notes |",
            "|---|---|---|---|---|---|---|---|---|"]
    for name, cls in adapters.items():
        row = data["registered"].get(name)
        if row:
            status, verified, text = STATUSES.get(row["status"], "invalid status"), _verified(row), _text(row)
        else:
            status, verified, text = NO_STATUS, "—", ""
        hosts = ", ".join(f"`{h}`" for h in adapter_hosts(cls, row))
        out.append("| " + " | ".join(_cell(c) for c in (
            cls.display_name or name, f"`{name}`", ", ".join(cls.languages),
            ", ".join(cls.content_types), hosts, _flags(cls), status, verified, text)) + " |")
    known = set(adapters)
    orphans = [n for n in data["registered"] if n not in known]
    if orphans:
        out += ["", "Rows in `docs/source-status.json` with no registered adapter: "
                + ", ".join(f"`{n}`" for n in orphans) + "."]

    out += ["", "## Generic paste-a-URL (no adapter) — confirmed on real, specific sites", "",
            "These went through `sources.preflight.preflight()` / the generic importer",
            "directly, not a dedicated adapter. Re-check before relying on them for a",
            "different site with the same template — \"generic works\" doesn't mean",
            "every WordPress/reader-template site does.", "",
            "| Site | Language | Type | Status | Verified | Notes |", "|---|---|---|---|---|---|"]
    for key, row in data["generic"].items():
        out.append("| " + " | ".join(_cell(c) for c in (
            row.get("label") or key, row.get("language"), row.get("type"),
            STATUSES.get(row["status"], "invalid status"), _verified(row), _text(row))) + " |")

    out += ["", "## Checked and set aside — not a site problem, a fit/legitimacy problem", "",
            "| Site | Status | Verified | Reason |", "|---|---|---|---|"]
    for key, row in data["set_aside"].items():
        out.append("| " + " | ".join(_cell(c) for c in (
            row.get("label") or key, STATUSES.get(row["status"], "invalid status"),
            _verified(row), _text(row))) + " |")
    out += ["", END]
    return "\n".join(out)


def splice(doc: str, block: str) -> str:
    """Put `block` between the markers; if the doc has none yet, it replaces the
    hand-maintained tables (everything from the first '## Registered adapters'
    heading up to the SFACG section)."""
    if BEGIN in doc and END in doc:
        head, rest = doc.split(BEGIN, 1)
        _, tail = rest.split(END, 1)
        return head + block + tail
    start = doc.index("## Registered adapters")
    stop = doc.index("## SFACG")
    return doc[:start] + block + "\n\n" + doc[stop:]


def render(doc_path=DOC_PATH, data_path=DATA_PATH) -> str:
    doc = Path(doc_path).read_text(encoding="utf-8")
    return splice(doc, build_block(load_data(data_path), registered_adapters()))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if docs/known-working-sources.md is out of date")
    args = ap.parse_args(argv)
    problems = data_problems(load_data())
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    new = render()
    old = DOC_PATH.read_text(encoding="utf-8")
    if args.check:
        if new != old:
            print("docs/known-working-sources.md is out of date: "
                  "run python scripts/source_status.py", file=sys.stderr)
            return 1
        return 0
    if new != old:
        DOC_PATH.write_text(new, encoding="utf-8", newline="\n")
        print("updated docs/known-working-sources.md")
    else:
        print("docs/known-working-sources.md already up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
