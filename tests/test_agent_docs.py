"""Guards for AGENTS.md, the only rules file OpenCode and other local models load.

It must stay short enough for a small context window and must not point at files
that no longer exist.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "AGENTS.md"
TABLE_HEADING = "## Where to look"
# The area table is generated-looking reference data and is large by design; every
# other section is guidance a model must read in full, so only that part is capped.
MAX_PROSE_BYTES = 10 * 1024
MAX_PROSE_LINES = 100
PATH_SUFFIXES = (".py", ".md", ".json", ".ps1", ".ts", ".tsx", ".txt")


def _prose_and_table(text: str):
    start = text.index(TABLE_HEADING)
    nxt = re.search(r"^## ", text[start + len(TABLE_HEADING):], re.M)
    end = start + len(TABLE_HEADING) + nxt.start() if nxt else len(text)
    return text[:start] + text[end:], text[start:end]


def test_agents_md_prose_is_small():
    prose, _ = _prose_and_table(AGENTS.read_text(encoding="utf-8"))
    size, lines = len(prose.encode("utf-8")), prose.count("\n") + 1
    assert size <= MAX_PROSE_BYTES and lines <= MAX_PROSE_LINES, (
        f"AGENTS.md outside the '{TABLE_HEADING}' section is {size} bytes / {lines} lines "
        f"(limit {MAX_PROSE_BYTES} bytes / {MAX_PROSE_LINES} lines). A small-context model reads all of it "
        "every session: cut text or move detail into docs/ and link it.")


def _named_paths(text: str):
    for token in re.findall(r"`([^`\n]+)`", text):
        # Commands, placeholders and globs are not paths. Only a folder (trailing /) or a
        # name with a file suffix counts, so module names like services/foo and "n/a" are left alone.
        if re.search(r"[\s<>*(]", token) or token in PATH_SUFFIXES:
            continue
        if token.endswith("/") or token.endswith(PATH_SUFFIXES):
            yield token


def test_agents_md_named_paths_exist():
    text = AGENTS.read_text(encoding="utf-8")
    missing = sorted({p for p in _named_paths(text)
                      if not (ROOT / p.rstrip("/")).exists()})
    assert missing == [], (
        f"AGENTS.md names paths that do not exist: {missing}. Fix the path, or write it without "
        "backticks if it is deliberately not a file.")
