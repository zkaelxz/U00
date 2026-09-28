"""
Resolve the append-only merge conflicts when merging origin/baihe-subtitler into a slice branch.

usage: resolve_slice.py <service_stem> <router_stem>
  e.g. resolve_slice.py translate_run translate_run_routes   (services/translate_run_service.py)

- api/server.py: take the base version and add this slice's router (import name + include_router).
- FILE_ORGANIZATION.md: take the base version and append this slice's services/ and api/routers/ entries
  (extracted from this branch's own version) as the new last entries, fixing tree connectors.
- api/schemas.py, docs/migration-review.md: keep BOTH sides of every conflict (base first, then branch).
Run from the repo root while a conflicted merge is in progress.
"""
import re
import subprocess
import sys

svc, router = sys.argv[1], sys.argv[2]
COL = 34  # description text starts at this column in the FILE_ORGANIZATION.md tree


def git_show(ref_path):
    return subprocess.run(["git", "show", ref_path], capture_output=True, text=True, check=True).stdout


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
        out = out.replace("    return app\n", inc + "    return app\n")
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
    open("FILE_ORGANIZATION.md", "w", encoding="utf-8").write(add_entries(base, branch))
    print("FILE_ORGANIZATION.md rebuilt from base + entries for", svc, router)


def merge_base():
    return subprocess.run(["git", "merge-base", "HEAD", "origin/baihe-subtitler"],
                          capture_output=True, text=True, check=True).stdout.strip()


def fix_schemas():
    base = git_show("origin/baihe-subtitler:api/schemas.py")
    mb = git_show(merge_base() + ":api/schemas.py")
    tip = git_show("HEAD:api/schemas.py")
    cut = lambda t: t[t.index("API_VERSION ="):]  # ignore the import block (branches only widened imports)
    rest_mb, rest_tip = cut(mb), cut(tip)
    if not rest_tip.startswith(rest_mb):
        raise SystemExit("schemas.py: branch did not purely append after the merge-base; resolve by hand")
    add = rest_tip[len(rest_mb):].strip("\n")
    out = base.rstrip("\n") + "\n\n\n" + add + "\n"
    open("api/schemas.py", "w", encoding="utf-8").write(out)
    print("api/schemas.py = base + branch appended block (%d lines)" % add.count("\n"))


def fix_docs():
    import difflib
    base = git_show("origin/baihe-subtitler:docs/migration-review.md")
    mb = git_show(merge_base() + ":docs/migration-review.md").split("\n")
    tip = git_show("HEAD:docs/migration-review.md").split("\n")
    ins = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, mb, tip, autojunk=False).get_opcodes():
        if tag in ("insert", "replace"):
            ins += tip[j1:j2]
    add = "\n".join(ins).strip("\n")
    marker = "**Next candidates:**"
    k = base.index(marker)
    out = base[:k] + add + "\n\n" + base[k:]
    open("docs/migration-review.md", "w", encoding="utf-8").write(out)
    print("docs/migration-review.md = base + branch paragraph (%d lines)" % (add.count("\n") + 1))


fix_server()
fix_file_org()
fix_schemas()
fix_docs()
