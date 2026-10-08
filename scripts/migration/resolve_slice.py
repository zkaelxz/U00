"""
Resolve the append-only merge conflicts when merging origin/baihe-subtitler into a slice branch.

usage: resolve_slice.py <service_stem> <router_stem>
  e.g. resolve_slice.py translate_run translate_run_routes   (services/translate_run_service.py)

- api/server.py: take the base version and add this slice's router (import name + include_router).
- FILE_ORGANIZATION.md: take the base version and append this slice's services/ and api/routers/ entries
  (extracted from this branch's own version) as the new last entries, fixing tree connectors.
- api/schemas/*.py: in each conflicted domain module keep BOTH sides of every conflict (base first, then branch,
  as before). A branch that still carries the old single api/schemas.py must be moved into the package by hand.
Run from the repo root while a conflicted merge is in progress.
"""
import re
import subprocess
import sys

svc = router = None  # set from argv in main
COL = 34  # description text starts at this column in the FILE_ORGANIZATION.md tree


def git_show(ref_path):
    return subprocess.run(["git", "show", ref_path], capture_output=True, text=True, encoding="utf-8", errors="replace", check=True).stdout


def keep_both(path):
    s = open(path, encoding="utf-8").read()
    pat = re.compile(r"<<<<<<< [^\n]*\n(.*?)=======\n(.*?)>>>>>>> [^\n]*\n", re.S)

    def fix(m):
        ours, theirs = m.group(1), m.group(2)
        if not theirs.endswith("\n"):
            theirs += "\n"
        if not ours.endswith("\n"):
            ours += "\n"
        return theirs + ("" if theirs.endswith("\n\n") else "\n") + ours

    new, n = pat.subn(fix, s)
    open(path, "w", encoding="utf-8").write(new)
    print(path, "keep-both blocks:", n)


def fix_server():
    base = git_show("origin/baihe-subtitler:api/server.py")
    m = re.search(r"from api\.routers import \((.*?)\)\n", base, re.S)
    names = [n.strip() for n in m.group(1).replace("\n", " ").split(",") if n.strip()]
    if router not in names:
        names.append(router)
    names.sort()
    block = "from api.routers import (\n" + "".join(f"    {n},\n" for n in names) + ")\n"
    out = base[: m.start()] + block + base[m.end():]
    inc = f"    app.include_router({router}.router)\n"
    if inc not in out:
        # Before the frontend catch-all, which must stay last (a route added
        # after it is shadowed: its paths answer 405/index.html).
        anchor = "    if settings.serve_frontend:\n"
        out = out.replace(anchor if anchor in out else "    return app\n",
                          inc + (anchor if anchor in out else "    return app\n"), 1)
    open("api/server.py", "w", encoding="utf-8").write(out)
    print("api/server.py rebuilt from base +", router)


def entry_block(lines, name, prefix_first):
    """Return (first-line-text-after-connector, [continuation texts]) for the tree entry naming `name`."""
    for i, ln in enumerate(lines):
        if ln.startswith(prefix_first):
            m = re.match(r"^" + re.escape(prefix_first) + r"(\S+)\s*(.*)$", ln)
            if not m or m.group(1) != name:
                continue
            head, first = m.group(1), m.group(2)
            cont = []
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                if nxt[:COL].strip("│ ") == "" and len(nxt) > COL and nxt[COL:].strip():
                    cont.append(nxt[COL:])
                    j += 1
                else:
                    break
            return head, first, cont
    return None


def add_entries(base, branch):
    b_lines = base.split("\n")
    br_lines = branch.split("\n")
    out = b_lines[:]

    def process(pfx_mid, pfx_last, cont_mid, cont_last, filename, header):
        nonlocal out
        blk = None
        for pf in (pfx_mid, pfx_last):
            blk = entry_block(br_lines, filename, pf)
            if blk:
                break
        if not blk:
            raise SystemExit(f"could not find {filename} entry in branch FILE_ORGANIZATION.md")
        head, first, cont = blk
        # locate the base's current last entry at this depth, INSIDE the subtree named by `header`
        h = next(i for i, ln in enumerate(out) if ln.startswith(header[0]) and header[1] in ln)
        end = next((i for i in range(h + 1, len(out))
                    if out[i].startswith("├── ") or out[i].startswith("└── ") or out[i] == "│"), len(out))
        last_idx = max(i for i in range(h + 1, end) if out[i].startswith(pfx_last))
        # only entries inside the same subtree: stop at the first blank-tree line after it
        k = last_idx
        # convert last -> middle
        out[k] = pfx_mid + out[k][len(pfx_last):]
        k += 1
        while k < len(out) and out[k][:COL].strip("│ ") == "" and len(out[k]) > COL and out[k][COL:].strip():
            out[k] = cont_mid + out[k][COL:]
            k += 1
        new = [pfx_last + head.ljust(COL - len(pfx_last) - 1) + " " + first]
        new += [cont_last + t for t in cont]
        out[k:k] = new

    process("│   ├── ", "│   └── ", "│   │" + " " * 29, "│" + " " * 33, f"{svc}_service.py", ("├── ", "services/"))
    if not any(ln.split()[-0:] and f"── {router}.py" in ln for ln in b_lines):  # router already listed: nothing to add
        process("│       ├── ", "│       └── ", "│       │" + " " * 25, "│" + " " * 33, f"{router}.py", ("│   └── ", "routers/"))
    return "\n".join(out)


def fix_file_org():
    base = git_show("origin/baihe-subtitler:FILE_ORGANIZATION.md")
    branch = git_show("HEAD:FILE_ORGANIZATION.md")
    out = add_entries(base, branch)
    open("FILE_ORGANIZATION.md", "w", encoding="utf-8").write(out)
    print("FILE_ORGANIZATION.md rebuilt from base + entries for", svc, router)
    # Only services/ and api/routers/ entries are carried over; list any other
    # line this branch added (frontend/, tests/, ...) so it is re-added by hand.
    mb = subprocess.run(["git", "merge-base", "HEAD", "MERGE_HEAD"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
    before = set(git_show(f"{mb}:FILE_ORGANIZATION.md").splitlines()) if mb else set()
    kept = set(out.splitlines())
    lost = [ln for ln in branch.splitlines() if ln not in before and ln not in kept]
    if lost:
        print("WARNING: branch lines not carried over; re-add them by hand:")
        for ln in lost:
            print("   ", ln)


def conflicted_schema_modules():
    out = subprocess.run(["git", "diff", "--name-only", "--diff-filter=U"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", check=True).stdout.split()
    return [p for p in out if p.startswith("api/schemas/") and p.endswith(".py")]


def fix_schemas():
    # The old monolith is gone from base; a branch that edits it cannot be merged mechanically.
    if subprocess.run(["git", "cat-file", "-e", "HEAD:api/schemas.py"], capture_output=True).returncode == 0:
        raise SystemExit("api/schemas.py: this branch still edits the old single file; move its new models "
                         "into the matching api/schemas/<module>.py by hand")
    paths = conflicted_schema_modules()
    for path in paths:
        keep_both(path)
    if not paths:
        print("api/schemas/: no conflicted module")


def main():
    global svc, router
    svc, router = sys.argv[1], sys.argv[2]
    fix_server()
    fix_file_org()
    fix_schemas()


if __name__ == "__main__":
    main()
