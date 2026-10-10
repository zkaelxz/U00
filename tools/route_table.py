"""Print the route -> permission table for docs/route-permissions.md, derived from the decorators.

    python tools/route_table.py            # print the table
    python tools/route_table.py --write    # rewrite only the table in docs/route-permissions.md

Builds the auth-on app with a built frontend exactly as
tests/test_api_permissions.py does (it imports that test's helpers) and never
starts a server. Run it after adding or changing a route; nobody edits rows by hand.
"""
import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "route-permissions.md"
HEADER = "| Route | Declaration |"


def build_table():
    sys.path.insert(0, str(ROOT))
    from tests.test_api_permissions import _app, make_fake_dist, route_declarations
    with tempfile.TemporaryDirectory() as tmp:
        declarations = route_declarations(_app("on", make_fake_dist(Path(tmp) / "dist")))
    # Sorted by path, then method, as the doc's header promises.
    rows = sorted(declarations, key=lambda r: (r.split(" ", 1)[1], r.split(" ", 1)[0]))
    return "\n".join([HEADER, "|---|---|"]
                     + [f"| `{r}` | {declarations[r]} |" for r in rows]) + "\n"


def replace_table(doc, table):
    """`doc` with its table (header line through the last `|` row) swapped for `table`."""
    start = doc.index(HEADER)
    lines = doc[start:].splitlines(keepends=True)
    rows = next((i for i, l in enumerate(lines) if not l.startswith("|")), len(lines))
    return doc[:start] + table + "".join(lines[rows:])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true", help="rewrite the table in docs/route-permissions.md")
    args = ap.parse_args(argv)
    table = build_table()
    if not args.write:
        sys.stdout.write(table)
        return 0
    doc = DOC.read_text(encoding="utf-8")
    new = replace_table(doc, table)
    if new != doc:
        DOC.write_text(new, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
