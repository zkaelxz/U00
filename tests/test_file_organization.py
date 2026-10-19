"""FILE_ORGANIZATION.md must stay in step with the modules it documents.

Mirrors .claude/hooks/file-organization-check.py (a module is "listed" when its
basename appears in the doc), plus the reverse check so renamed or deleted
modules do not leave stale lines behind. No per-file lists live here.
"""
import os
import re
from collections import Counter

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(PROJECT_ROOT, "FILE_ORGANIZATION.md")
WATCHED_DIRS = ("", "lib", "services", "api", "api/schemas", "api/routers", "db", "jobs", "engine_backends",
                "sources", "sources/adapters", "scripts", "tools", "installer")


def _doc_text():
    with open(DOC, encoding="utf-8") as fh:
        return fh.read()


def _watched_modules():
    """Repo-relative paths of non-test .py files in the directories the doc covers."""
    found = []
    for rel_dir in WATCHED_DIRS:
        folder = os.path.join(PROJECT_ROOT, rel_dir) if rel_dir else PROJECT_ROOT
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if (name.endswith(".py") and name != "__init__.py" and not name.startswith("test_")
                    and os.path.isfile(path)):
                found.append(f"{rel_dir}/{name}" if rel_dir else name)
    return found


SECTIONS = {  # heading -> folder its entries live in
    "## Top-level modules": "",
    "## lib/": "lib",
    "## services/": "services",
    "## api/": "api",
    "### api/schemas/": "api/schemas",
    "### api/routers/": "api/routers",
    "## engine_backends/": "engine_backends",
    "## jobs/": "jobs",
    "## sources/": "sources",
    "### sources/adapters/": "sources/adapters",
    "## scripts/": "scripts",
    "## tools/": "tools",
    "## installer/": "installer",
}


def _section_entries():
    """(folder, name) for each backticked .py name in the module-list sections.

    Bold group labels and prose lines are not entries.
    """
    entries = []
    current = None
    for line in _doc_text().splitlines():
        if line.startswith("#"):
            current = SECTIONS.get(line.strip())
            continue
        if current is None:
            continue
        # Only the first name of a bullet is the entry; later ones in its description are mentions.
        bullet = re.match(r"- `([A-Za-z0-9_]+\.py)`", line)
        if bullet:
            entries.append((current, bullet.group(1)))
    return entries


SECTIONS = {  # heading -> folder its entries live in
    "## Top-level modules": "",
    "## lib/": "lib",
    "## services/": "services",
    "## api/": "api",
    "### api/schemas/": "api/schemas",
    "### api/routers/": "api/routers",
    "## engine_backends/": "engine_backends",
    "## jobs/": "jobs",
    "## sources/": "sources",
    "### sources/adapters/": "sources/adapters",
    "## scripts/": "scripts",
    "## tools/": "tools",
    "## installer/": "installer",
}


def _section_entries():
    """(folder, name) for each backticked .py name in the module-list sections.

    Bold group labels and prose lines are not entries.
    """
    entries = []
    current = None
    for line in _doc_text().splitlines():
        if line.startswith("#"):
            current = SECTIONS.get(line.strip())
            continue
        if current is None:
            continue
        # Only the first name of a bullet is the entry; later ones in its description are mentions.
        bullet = re.match(r"- `([A-Za-z0-9_]+\.py)`", line)
        if bullet:
            entries.append((current, bullet.group(1)))
    return entries


def test_every_module_is_listed():
    listed = {f"{d + '/' if d else ''}{n}" for d, n in _section_entries()}
    listed |= {p for p in _watched_modules() if p.startswith("db/") and f"`{p}`" in _doc_text()}
    unlisted = [p for p in _watched_modules() if p not in listed]
    assert unlisted == [], (
        f"not listed in FILE_ORGANIZATION.md: {unlisted}. Add each to the right group "
        "in the same change."
    )


def test_no_stale_entries():
    stale = sorted(f"{d + '/' if d else ''}{n}" for d, n in set(_section_entries())
                   if not os.path.isfile(os.path.join(PROJECT_ROOT, d, n)))
    assert stale == [], (
        f"FILE_ORGANIZATION.md names files that no longer exist: {stale}. "
        "Remove or rename these lines."
    )


def test_no_duplicate_entries():
    counts = Counter(_section_entries())
    dupes = sorted(f"{d + '/' if d else ''}{n}" for (d, n), c in counts.items() if c > 1)
    assert dupes == [], (
        f"duplicate FILE_ORGANIZATION.md entries: {dupes}. Keep one line per file."
    )
