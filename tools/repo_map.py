"""Print a symbol map of the repo, sized for a model with a small context window.

    python tools/repo_map.py                 # top-level map: module -> purpose, by layer
    python tools/repo_map.py db              # one module's signatures (or its part index)
    python tools/repo_map.py db --part 4     # one part of an oversized module
    python tools/repo_map.py services        # a package: each module's symbol names
    python tools/repo_map.py --find "repeat" # search names, signatures and docstrings

Output is generated on demand and never committed: a committed listing would
conflict whenever two branches change a signature. Stdlib only, so it runs
before the app's dependencies are installed. Python is read with ast; the
frontend (frontend/src/api, frontend/src/types, the workspace stages) is read
with line regexes, so a multi-line export shows only its first line.
"""
import argparse
import ast
import fnmatch
import functools
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# A listing over this (~6k tokens at ~4 bytes per token) is split into parts, so
# one read stays well under a 64K window with room left for the code itself.
BUDGET_BYTES = 24_000
SKIP_DIRS = {".git", "__pycache__", "node_modules", "tests", "library", "model_cache", "venv",
             ".venv", "env", "build", "dist", "tmp", "smoke_pack", "test-results", "frontend"}
FRONTEND_DIRS = ("frontend/src/api", "frontend/src/types", "frontend/src/pages/workspace/stages")
DOC_CHARS = 100
EXPR_CHARS = 40
FIND_SIG_CHARS = 200

# Literals that look like keys, tokens or absolute machine paths are masked so a
# default value or docstring can't leak one into a model's context or a log.
_SECRET_RE = re.compile(r"sk-[A-Za-z0-9_\-]{16,}|AIza[A-Za-z0-9_\-]{30,}|(?:gh[pousr]_|hf_)[A-Za-z0-9]{20,}"
                        r"|xox[abpr]-[A-Za-z0-9\-]{10,}")
# A long run mixing upper case, lower case and digits reads as a token; a long
# snake_case identifier in a docstring does not, and must stay readable.
_TOKEN_RE = re.compile(r"[A-Za-z0-9+=_\-]{32,}")
_ABS_PATH_RE = re.compile(r"(?<![\w.])(?:[A-Za-z]:[\\/]{1,2}|/(?:home|Users|root|tmp|usr|var|etc|opt|mnt)/)"
                          r"[^\s'\")]*")
_BANNER_RE = re.compile(r"^#\s?[-=#]{10,}\s*$")
_CONST_RE = re.compile(r"^_?[A-Z][A-Z0-9_]*$")


def _mask_token(m) -> str:
    run = m.group(0)
    mixed = any(c.isupper() for c in run) and any(c.islower() for c in run) and any(c.isdigit() for c in run)
    return "<redacted>" if mixed else run


def redact(text: str) -> str:
    return _ABS_PATH_RE.sub("<path>", _TOKEN_RE.sub(_mask_token, _SECRET_RE.sub("<redacted>", text)))


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _expr(node, limit=EXPR_CHARS) -> str:
    return _clip(redact(ast.unparse(node)), limit)


# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------

def _claudeignore():
    path = ROOT / ".claudeignore"
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith(("#", "!"))]


def is_ignored(rel: str, patterns) -> bool:
    """gitignore-lite: 'dir/' excludes a folder anywhere, anything else is a glob
    matched against the path and the file name. Negation is not supported."""
    for pat in patterns:
        if pat.endswith("/"):
            if ("/" + rel + "/").find("/" + pat.strip("/") + "/") != -1:
                return True
        elif fnmatch.fnmatch(rel, pat.lstrip("/")) or fnmatch.fnmatch(rel.rsplit("/", 1)[-1], pat):
            return True
    return False


def _walk(base: Path, skip, suffixes):
    for folder, dirs, names in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in skip and not d.startswith("."))
        for name in sorted(names):
            if os.path.splitext(name)[1] in suffixes:
                yield Path(folder) / name


def source_files():
    """Repo-relative paths of every file the map covers, sorted."""
    patterns = _claudeignore()
    out = []
    for path in _walk(ROOT, SKIP_DIRS, {".py"}):
        rel = path.relative_to(ROOT).as_posix()
        if not path.name.startswith("test_") and path.name != "conftest.py" and not is_ignored(rel, patterns):
            out.append(rel)
    for folder in FRONTEND_DIRS:
        if not (ROOT / folder).is_dir():
            continue
        for path in _walk(ROOT / folder, {"node_modules"}, {".ts", ".tsx"}):
            rel = path.relative_to(ROOT).as_posix()
            if ".test." not in path.name and not is_ignored(rel, patterns):
                out.append(rel)
    return sorted(set(out))


# --------------------------------------------------------------------------
# Python
# --------------------------------------------------------------------------

def _arg(arg: ast.arg, default=None) -> str:
    text = arg.arg
    if arg.annotation is not None:
        text += ": " + _expr(arg.annotation)
        if default is not None:
            text += " = " + _expr(default)
    elif default is not None:
        text += "=" + _expr(default)
    return text


def render_args(a: ast.arguments) -> str:
    positional = a.posonlyargs + a.args
    defaults = [None] * (len(positional) - len(a.defaults)) + list(a.defaults)
    parts = []
    for i, (arg, default) in enumerate(zip(positional, defaults)):
        parts.append(_arg(arg, default))
        if a.posonlyargs and i == len(a.posonlyargs) - 1:
            parts.append("/")
    if a.vararg:
        parts.append("*" + _arg(a.vararg))
    elif a.kwonlyargs:
        parts.append("*")
    parts += [_arg(arg, default) for arg, default in zip(a.kwonlyargs, a.kw_defaults)]
    if a.kwarg:
        parts.append("**" + _arg(a.kwarg))
    return ", ".join(parts)


def render_def(node, qualname=None) -> str:
    decos = "".join("@" + _expr(d, 80) + " " for d in node.decorator_list)
    name = qualname or node.name
    if isinstance(node, ast.ClassDef):
        bases = [_expr(b) for b in node.bases] + [f"{k.arg}={_expr(k.value)}" for k in node.keywords]
        return f"{decos}class {name}" + (f"({', '.join(bases)})" if bases else "")
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    ret = f" -> {_expr(node.returns)}" if node.returns is not None else ""
    return f"{decos}{prefix} {name}({render_args(node.args)}){ret}"


def _doc_line(node) -> str:
    """First sentence of the docstring's first paragraph (docstrings wrap mid-sentence)."""
    doc = (ast.get_docstring(node, clean=True) or "").strip()
    para = " ".join(doc.split("\n\n", 1)[0].split())
    return _clip(redact(re.split(r"(?<=[.!?])\s", para, maxsplit=1)[0]), DOC_CHARS)


class Symbol:
    __slots__ = ("qualname", "line", "end", "sig", "doc", "fulldoc", "depth", "fields")

    def __init__(self, node, qualname, depth):
        self.qualname = qualname
        self.line = node.lineno
        self.end = getattr(node, "end_lineno", node.lineno)
        self.sig = render_def(node, qualname)
        self.doc = _doc_line(node)
        self.fulldoc = redact(ast.get_docstring(node) or "")
        self.depth = depth
        self.fields = []

    def render(self) -> str:
        line = f"{'  ' * self.depth}{self.line}-{self.end} {self.sig}"
        if self.doc:
            line += "  # " + self.doc
        if self.fields:
            line += "\n" + "  " * (self.depth + 1) + "fields: " + ", ".join(self.fields)
        return line


def _collect(body, prefix, depth, out):
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            sym = Symbol(node, prefix + node.name, depth)
            out.append(sym)
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                        sym.fields.append(item.target.id)
                _collect(node.body, prefix + node.name + ".", depth + 1, out)


class Module:
    def __init__(self, rel):
        self.rel = rel
        self.purpose = ""
        self.symbols = []
        self.constants = []
        self.sections = []
        self.error = ""
        source = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        if rel.endswith(".py"):
            self._parse_python(source)
        else:
            self._parse_frontend(source)

    def _parse_python(self, source):
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            self.error = f"could not parse (line {exc.lineno})"
            return
        # Most docstrings open with "path/name.py -- "; the map already shows the path.
        self.purpose = re.sub(r"^(?:[\w./]*/)?" + re.escape(Path(self.rel).stem) + r"(?:\.py)?\s*(?:--|:|-)\s*",
                              "", _doc_line(tree))
        _collect(tree.body, "", 0, self.symbols)
        for node in tree.body:
            targets = node.targets if isinstance(node, ast.Assign) else [getattr(node, "target", None)]
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                self.constants += [f"{t.id}:{node.lineno}" for t in targets
                                   if isinstance(t, ast.Name) and _CONST_RE.match(t.id)]
        lines = source.splitlines()
        for i, text in enumerate(lines[:-1]):
            title = lines[i + 1].strip()
            # A comment line just above means this is the closing rule of a banner.
            opening = i == 0 or not lines[i - 1].lstrip().startswith("#")
            if opening and _BANNER_RE.match(text) and title.startswith("#") and not _BANNER_RE.match(title):
                self.sections.append((i + 1, _clip(title.lstrip("# "), 70)))

    def _parse_frontend(self, source):
        lines = source.splitlines()
        if lines and lines[0].lstrip().startswith(("//", "/*")):
            self.purpose = _clip(redact(lines[0].strip().lstrip("/* ")), DOC_CHARS)
        i = 0
        while i < len(lines):
            text = lines[i]
            m = re.match(r"export\s+(?:default\s+)?(?:declare\s+)?(?:async\s+)?"
                         r"(function\*?|const|let|class|interface|type|enum)\s+(\w+)", text)
            if not m:
                i += 1
                continue
            sym = _FrontendSymbol(m.group(2), i + 1, _clip(redact(text.rstrip(" {")), 160))
            self.symbols.append(sym)
            # Exported API objects (`export const dubApi = {`) list their members and
            # the /api path each one calls, so a model can jump to the router.
            if m.group(1) == "const" and text.rstrip().endswith("{"):
                j = i + 1
                while j < len(lines) and not lines[j].startswith("}"):
                    member = re.match(r"^  (\w+)\s*[:(]", lines[j])
                    if member:
                        block = " ".join(lines[j:j + 4])
                        url = re.search(r"[`'\"](/api/[^`'\"?]*)", block)
                        path = re.sub(r"\$\{[^}]*\}", "{}", url.group(1)).split("${")[0] if url else ""
                        sym.members.append(member.group(1) + (f" -> {path}" if path else ""))
                    j += 1
                sym.end = j + 1
                i = j
            i += 1


def load_module(rel: str) -> "Module":
    return _load_module(ROOT, rel)


@functools.lru_cache(maxsize=None)
def _load_module(root, rel):
    return Module(rel)


class _FrontendSymbol:
    def __init__(self, name, line, sig):
        self.qualname, self.line, self.end, self.sig = name, line, line, sig
        self.doc, self.fulldoc, self.depth, self.members = "", "", 0, []

    def render(self) -> str:
        head = f"{self.line} {self.sig}"
        return head + "".join(f"\n  {m}" for m in self.members)


# --------------------------------------------------------------------------
# Listings
# --------------------------------------------------------------------------

def module_header(mod: Module) -> str:
    return f"## {mod.rel}" + (f" -- {mod.purpose}" if mod.purpose else "") + (f" [{mod.error}]" if mod.error else "")


def module_body(mod: Module, symbols=None) -> str:
    symbols = mod.symbols if symbols is None else symbols
    out = [s.render() for s in symbols]
    if mod.constants and symbols is mod.symbols:
        out.append("constants: " + ", ".join(mod.constants))
    return "\n".join(out)


def split_parts(mod: Module):
    """Top-level symbol groups for an oversized module: one per '# ----' banner
    section, then cut further in source order until each fits the budget.
    Methods follow their class into whichever part the class lands in."""
    starts = [(1, "top of file")] + mod.sections
    class_line = {s.qualname: s.line for s in mod.symbols if s.depth == 0}
    parts = []
    for idx, (start, title) in enumerate(starts):
        stop = starts[idx + 1][0] if idx + 1 < len(starts) else float("inf")
        chunk, size = [], 0
        for sym in mod.symbols:
            if not start <= class_line.get(sym.qualname.split(".", 1)[0], sym.line) < stop:
                continue
            cost = len(sym.render().encode()) + 1
            if chunk and sym.depth == 0 and size + cost > BUDGET_BYTES * 0.9:
                parts.append((title, chunk))
                chunk, size = [], 0
            chunk.append(sym)
            size += cost
        if chunk:
            parts.append((title, chunk))
    return parts


def print_module(mod: Module, part=None):
    print(module_header(mod))
    body = module_body(mod)
    if len(body.encode()) <= BUDGET_BYTES and part is None:
        print(body)
        return
    parts = split_parts(mod)
    if part is None:
        print(f"Too large to list at once (~{len(body.encode()) // 4} tokens). Parts "
              f"(python tools/repo_map.py {mod.rel} --part N):")
        for n, (title, syms) in enumerate(parts, 1):
            names = [s.qualname for s in syms if s.depth == 0]
            print(f"{n:>3}. {title} [{syms[0].line}-{syms[-1].end}, {len(names)} defs]: "
                  + _clip(", ".join(names), 150))
        if mod.constants:
            print("constants: " + ", ".join(mod.constants))
        return
    if not 1 <= part <= len(parts):
        sys.exit(f"{mod.rel} has parts 1-{len(parts)}")
    title, syms = parts[part - 1]
    print(f"Part {part}/{len(parts)}: {title}")
    print(module_body(mod, syms))


def _chunks(blocks):
    parts, current, size = [], [], 0
    for block in blocks:
        if current and size + len(block.encode()) > BUDGET_BYTES:
            parts.append(current)
            current, size = [], 0
        current.append(block)
        size += len(block.encode()) + 1
    return parts + ([current] if current else [])


def print_package(folder: str, files, part=None):
    """The modules directly in `folder` with their top-level names; subfolders
    are listed by name so each one is its own read."""
    depth = folder.count("/") + 1
    direct = [r for r in files if r.startswith(folder + "/") and r.count("/") == depth]
    subdirs = sorted({r.split("/")[depth] for r in files if r.startswith(folder + "/") and r.count("/") > depth})
    blocks = []
    for rel in direct:
        mod = load_module(rel)
        names = [s.qualname for s in mod.symbols if s.depth == 0]
        blocks.append(module_header(mod) + ("\n  " + _clip(", ".join(names), 400) if names else ""))
    parts = _chunks(blocks)
    print(f"# {folder}/ -- {len(direct)} modules. Signatures: python tools/repo_map.py <module>")
    if subdirs:
        print("subfolders: " + ", ".join(f"{folder}/{d}" for d in subdirs))
    if part is None and len(parts) > 1:
        print(f"Too large to list at once. Parts (python tools/repo_map.py {folder} --part N):")
        for n, chunk in enumerate(parts, 1):
            first, last = (b.split("\n", 1)[0].split(" ", 2)[1] for b in (chunk[0], chunk[-1]))
            print(f"{n:>3}. {first} .. {last} ({len(chunk)} modules)")
        return
    if part is not None and not 1 <= part <= len(parts):
        sys.exit(f"{folder}/ has parts 1-{len(parts)}")
    print("\n".join(parts[(part or 1) - 1] if parts else []))


# --------------------------------------------------------------------------
# Top-level map
# --------------------------------------------------------------------------

LAYERS = (
    ("Frontend (React, read with regex)", lambda r: r.startswith("frontend/")),
    ("API core (api/)", lambda r: r.startswith("api/") and r.count("/") == 1 and not r.endswith("_schemas.py")),
    ("API routes (api/routers/*_routes.py)", lambda r: r.startswith("api/routers/")),
    ("API schemas (api/schemas/, api/*_schemas.py)",
     lambda r: r.startswith("api/schemas/") or (r.startswith("api/") and r.endswith("_schemas.py"))),
    ("Services (services/*_service.py)", lambda r: r.startswith("services/")),
    ("Translation engines (engine_backends/)", lambda r: r.startswith("engine_backends/")),
    ("Sources and site adapters (sources/)", lambda r: r.startswith("sources/")),
    ("Shared helpers (lib/, no domain knowledge)", lambda r: r.startswith("lib/")),
    ("Root domain modules", lambda r: "/" not in r and r not in ("db.py", "cli.py")),
    ("Database (db.py: never open whole; list it with `repo_map.py db`)", lambda r: r == "db.py"),
    ("Entry points and tooling (not the app's runtime logic)", lambda r: True),
)
# Layers whose file names say what they are print names only; their purposes
# are one step away (`repo_map.py services`), and listing them here would push
# the map well past the few-KB first read it is meant to be.
NAMES_ONLY = {"API routes (api/routers/*_routes.py)",
              "API schemas (api/schemas/, api/*_schemas.py)", "Services (services/*_service.py)",
              "Entry points and tooling (not the app's runtime logic)"}
NAMES_ONLY_DIRS = ("sources/adapters",)
MAP_PURPOSE_CHARS = 60


def print_map(files):
    print("# Repo map. Layers top to bottom; `python tools/repo_map.py <module|package>` lists "
          "signatures, `--find <text>` searches them.")
    remaining = list(files)
    for title, belongs in LAYERS:
        mine = [r for r in remaining if belongs(r) and not r.endswith("__init__.py")]
        remaining = [r for r in remaining if not belongs(r)]
        if not mine:
            continue
        print(f"\n## {title}")
        if title.startswith("Frontend"):
            for folder in FRONTEND_DIRS:
                count = sum(1 for r in mine if r.startswith(folder + "/"))
                print(f"{folder}/ ({count} files): python tools/repo_map.py {folder}")
            continue
        names_only = [r for r in mine if title in NAMES_ONLY or r.rpartition("/")[0] in NAMES_ONLY_DIRS]
        if names_only:
            by_dir = {}
            for rel in names_only:
                folder, _, name = rel.rpartition("/")
                by_dir.setdefault(folder, []).append(name.rsplit(".", 1)[0])
            for folder, names in by_dir.items():
                print(f"{folder or '.'}/: " + ", ".join(names))
        for rel in mine:
            if rel not in names_only:
                mod = load_module(rel)
                print(f"{rel}" + (f" -- {_clip(mod.purpose, MAP_PURPOSE_CHARS)}" if mod.purpose else ""))


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------

def find(text: str, files, limit: int):
    words = text.lower().split()
    hits = []
    for rel in files:
        mod = load_module(rel)
        if mod.purpose and all(w in (rel + " " + mod.purpose).lower() for w in words):
            hits.append((1, f"{rel}:1 (module) {mod.purpose}"))
        for sym in mod.symbols:
            extra = " ".join(getattr(sym, "fields", []) + getattr(sym, "members", []))
            hay = f"{rel} {sym.qualname} {sym.sig} {extra} {sym.fulldoc}".lower()
            if all(w in hay for w in words):
                name = sym.qualname.lower()
                rank = 0 if all(w in name for w in words) else 1 if all(w in f"{name} {sym.sig} {extra}".lower()
                                                                       for w in words) else 2
                line = f"{rel}:{sym.line}-{sym.end} {_clip(sym.sig, FIND_SIG_CHARS)}" + (f"  # {sym.doc}" if sym.doc else "")
                hits.append((rank, line))
        for const in mod.constants:
            name, _, line = const.partition(":")
            if all(w in name.lower() for w in words):
                hits.append((0, f"{rel}:{line} {name} (constant)"))
    hits.sort(key=lambda h: h[0])
    for _, line in hits[:limit]:
        print(line)
    if len(hits) > limit:
        print(f"... {len(hits) - limit} more; add a word or raise --limit")
    if not hits:
        print("no match; try a shorter word, or git grep for text inside function bodies")


# --------------------------------------------------------------------------

def resolve(target: str, files):
    """A module or package from a path, dotted name or unique file stem."""
    t = target.strip().rstrip("/").replace("\\", "/")
    t = re.sub(r"\.(py|tsx?)$", "", t)
    if "/" not in t and "." in t:
        t = t.replace(".", "/")
    for rel in files:
        if rel.rsplit(".", 1)[0] == t:
            return "module", rel
    if any(r.startswith(t + "/") for r in files):
        return "package", t
    stems = [r for r in files if r.rsplit("/", 1)[-1].rsplit(".", 1)[0] == t]
    if len(stems) == 1:
        return "module", stems[0]
    if stems:
        sys.exit(f"'{target}' is ambiguous: " + ", ".join(stems))
    sys.exit(f"no module or package '{target}' in the map; try --find")


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("target", nargs="?", help="module or package, e.g. db, services/drama_service, api/routers")
    parser.add_argument("--part", type=int, help="part number of an oversized module or package")
    parser.add_argument("--find", metavar="TEXT", help="words that must all appear in a name, signature or docstring")
    parser.add_argument("--limit", type=int, default=40, help="max --find results (default 40)")
    args = parser.parse_args(argv)
    files = source_files()
    if args.find:
        find(args.find, files, args.limit)
    elif args.target:
        kind, rel = resolve(args.target, files)
        if kind == "module":
            print_module(load_module(rel), args.part)
        else:
            print_package(rel, files, args.part)
    else:
        print_map(files)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        # Piped into `head`: stop quietly instead of printing a traceback.
        sys.stdout = open(os.devnull, "w")
