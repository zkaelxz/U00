"""
tests/test_static_analysis.py -- static checks that catch bugs no
compile check or unit test can, because they only ever fire on a
specific runtime code path.

This test module exists because of a real shipped bug: `source_language`
was read inside a "Raw novel" upload section before being assigned by a
selectbox that appeared later in the same function. It compiled fine
(orphaned/misordered code always does) and every existing test passed,
because nothing exercised that exact render path. Only a static
data-flow check catches this class of bug before a user does.
"""
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TABS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tabs")


def _find_use_before_def(node):
    """Walks a function body in execution order, flagging any Name read
    before it's been assigned earlier in the SAME linear scope. Skips
    into nested defs/lambdas/comprehensions rather than following them,
    since those legitimately close over names bound later (a callback
    built now, called after the surrounding code finishes)."""
    assigned = {a.arg for a in node.args.args}
    problems = []

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, n):
            assigned.add(n.name)

        def visit_Lambda(self, n):
            pass

        def visit_ListComp(self, n):
            pass

        def visit_SetComp(self, n):
            pass

        def visit_DictComp(self, n):
            pass

        def visit_GeneratorExp(self, n):
            pass

        def visit_With(self, n):
            for item in n.items:
                self.visit(item.context_expr)
                if item.optional_vars:
                    for t in ast.walk(item.optional_vars):
                        if isinstance(t, ast.Name):
                            assigned.add(t.id)
            for s in n.body:
                self.visit(s)

        def visit_Name(self, n):
            if isinstance(n.ctx, ast.Load):
                if n.id not in assigned:
                    problems.append((n.lineno, n.id))
            elif isinstance(n.ctx, ast.Store):
                assigned.add(n.id)

        def visit_Import(self, n):
            for alias in n.names:
                assigned.add((alias.asname or alias.name).split(".")[0])

        def visit_ImportFrom(self, n):
            for alias in n.names:
                assigned.add(alias.asname or alias.name)

        def visit_ExceptHandler(self, n):
            if n.name:
                assigned.add(n.name)
            for s in n.body:
                self.visit(s)

        def visit_For(self, n):
            self.visit(n.iter)
            for t in ast.walk(n.target):
                if isinstance(t, ast.Name):
                    assigned.add(t.id)
            for s in n.body + n.orelse:
                self.visit(s)

    v = Visitor()
    for stmt in node.body:
        v.visit(stmt)
    return problems


def _bound_names_from_import(n):
    """Names a single Import/ImportFrom node binds, e.g. `import x as y`
    binds `y`. Mirrors _find_use_before_def's own visit_Import/
    visit_ImportFrom handling, so "was this name assigned later" and
    "does an import count as assigning it" agree with each other."""
    if isinstance(n, ast.Import):
        return {(a.asname or a.name).split(".")[0] for a in n.names}
    if isinstance(n, ast.ImportFrom):
        return {a.asname or a.name for a in n.names if a.name != "*"}
    return set()


def _scan_tab_file(path):
    """Returns a list of (function_name, lineno, var_name) for names that
    are read before being assigned AND are also assigned somewhere later
    in the same function -- filtering out ordinary wildcard-imported
    names (st, db, etc.) from `from common import *`, which this static
    pass can't see and isn't trying to check.

    "Assigned somewhere later" must count a later `import x as name` the
    same way it counts `name = ...` -- a real shipped bug (tguide used
    early in render_workspace_tab, then re-imported as a local `import
    translation_guide as tguide` far below) was an exact use-before-def
    that _find_use_before_def correctly detected, but this filter used
    to only recognize ast.Name Store nodes as "assigned later", and an
    import produces an ast.alias, not an ast.Name -- so the real finding
    was silently dropped here before it ever reached anyone.
    """
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src, path)
    results = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name.startswith("render_")):
            continue
        for lineno, name in _find_use_before_def(node):
            has_later_assignment = any(
                (isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store) and n.id == name)
                or name in _bound_names_from_import(n)
                for n in ast.walk(node)
            )
            if has_later_assignment:
                results.append((node.name, lineno, name))
    return results


class TestScanTabFileCatchesLaterReimport:
    """Regression test for the tguide/UnboundLocalError bug: a name used
    early in a render_ function, then re-imported locally (`import x as
    name`) later in the SAME function, makes Python treat that name as
    local to the whole function -- the earlier use raises
    UnboundLocalError at runtime, the moment that code path actually
    runs. _find_use_before_def already caught this as a use-before-
    assignment; _scan_tab_file's own filter was silently dropping it
    because it only recognized plain `name = ...` as a later assignment,
    not a later import. This exercises the real, file-based path
    (_scan_tab_file), not just the lower-level detector, since that's
    where the bug actually was."""

    def test_catches_use_before_a_later_import_as(self, tmp_path):
        src = (
            "def render_x():\n"
            "    thing.do_something()\n"
            "    import something_else as thing\n"
        )
        f = tmp_path / "fake_tab.py"
        f.write_text(src, encoding="utf-8")
        problems = _scan_tab_file(str(f))
        assert any(name == "thing" for _, _, name in problems)

    def test_still_clean_when_the_import_comes_first(self, tmp_path):
        src = (
            "def render_x():\n"
            "    import something_else as thing\n"
            "    thing.do_something()\n"
        )
        f = tmp_path / "fake_tab.py"
        f.write_text(src, encoding="utf-8")
        assert _scan_tab_file(str(f)) == []


class TestNoUseBeforeDefinition:
    """One test per tab file, so a failure names exactly which file and
    line broke rather than a single opaque assertion covering all nine."""

    def _assert_clean(self, filename):
        path = os.path.join(TABS_DIR, filename)
        problems = _scan_tab_file(path)
        assert problems == [], (
            f"{filename} reads a name before it's assigned, later, in the same "
            f"render function -- this compiles fine but crashes at runtime the "
            f"moment that code path is reached: {problems}"
        )

    def test_workspace_tab(self):
        self._assert_clean("workspace_tab.py")

    def test_library_tab(self):
        self._assert_clean("library_tab.py")

    def test_reader_tab(self):
        self._assert_clean("reader_tab.py")

    def test_scanlate_tab(self):
        self._assert_clean("scanlate_tab.py")

    def test_navigator_tab(self):
        self._assert_clean("navigator_tab.py")

    def test_discover_tab(self):
        self._assert_clean("discover_tab.py")

    def test_settings_tab(self):
        self._assert_clean("settings_tab.py")

    def test_diagnostics_tab(self):
        self._assert_clean("diagnostics_tab.py")


class TestScannerItself:
    """The checker needs its own tests -- a static analyzer that's wrong
    is worse than none, since a false pass hides a real bug and a false
    fail trains people to ignore it."""

    def _check_src(self, src):
        tree = ast.parse(src, "<test>")
        fn = tree.body[0]
        assert isinstance(fn, ast.FunctionDef)
        problems = _find_use_before_def(fn)
        later = {name for _, name in problems
                 if any(isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store) and n.id == name
                        for n in ast.walk(fn))}
        return later

    def test_catches_the_actual_shipped_bug_pattern(self):
        src = (
            "def render_x():\n"
            "    with expander(f'{lang}'):\n"
            "        pass\n"
            "    lang = pick_language()\n"
        )
        assert "lang" in self._check_src(src)

    def test_normal_definition_order_is_clean(self):
        src = (
            "def render_x():\n"
            "    lang = pick_language()\n"
            "    with expander(f'{lang}'):\n"
            "        pass\n"
        )
        assert self._check_src(src) == set()

    def test_wildcard_import_names_are_not_flagged(self):
        # `st`, `db` etc. come from `from common import *` -- this checker
        # has no visibility into that and correctly ignores names that are
        # never assigned anywhere in the function itself.
        src = (
            "def render_x():\n"
            "    st.write('hello')\n"
        )
        assert self._check_src(src) == set()

    def test_lambda_closing_over_a_later_name_is_not_flagged(self):
        # A callback built now but only invoked after `count` is set later
        # is normal Streamlit code (e.g. a button's on_click), not a bug.
        src = (
            "def render_x():\n"
            "    cb = lambda: use(count)\n"
            "    count = 5\n"
        )
        assert self._check_src(src) == set()

    def test_for_loop_target_is_assigned_before_body_reads_it(self):
        src = (
            "def render_x():\n"
            "    for item in items:\n"
            "        use(item)\n"
        )
        assert "item" not in self._check_src(src)

    def test_with_statement_target_available_in_body(self):
        src = (
            "def render_x():\n"
            "    with opener() as f:\n"
            "        use(f)\n"
        )
        assert "f" not in self._check_src(src)

def _names_bound_by_module(path):
    """Top-level names a module binds via import/import-from/assignment.
    Parses the AST only -- doesn't require the module's own dependencies
    (streamlit, pandas, ...) to be installed, since nothing is executed."""
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src, path)
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name != "*":
                    names.add(a.asname or a.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        names.add(n.id)
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


def _find_undefined_bases(node, available):
    """Flags `X.attr` usage where X is never bound anywhere reachable:
    not a parameter, not assigned in the function, not imported in the
    file, and not provided by `from common import *`. Catches a name
    used but never imported ANYWHERE -- a different bug shape than
    use-before-def, where the name IS defined, just too late. This is
    the shape of two real bugs: `known_sites` used without being
    imported, and later `time.time()`/`time.sleep()` used the same way."""
    local = {a.arg for a in node.args.args}
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            local.add(n.id)
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                local.add((a.asname or a.name).split(".")[0])
        if isinstance(n, ast.ExceptHandler) and n.name:
            local.add(n.name)
        if isinstance(n, ast.Lambda):
            local |= {a.arg for a in n.args.args}
        if isinstance(n, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            for gen in n.generators:
                for t in ast.walk(gen.target):
                    if isinstance(t, ast.Name):
                        local.add(t.id)

    everything = available | local
    problems, seen = [], set()
    for n in ast.walk(node):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
            base = n.value.id
            if base not in everything and base not in seen:
                seen.add(base)
                problems.append((n.lineno, base, n.attr))
    return problems


class TestNoUndefinedNames:
    """A name used but never imported ANYWHERE in the file (not a
    use-before-def ordering issue -- genuinely never bound at all) still
    compiles and passes every test that doesn't happen to exercise that
    exact line. Only a static reachability check catches it first."""

    def _common_names(self):
        import builtins
        common_path = os.path.join(os.path.dirname(TABS_DIR), "common.py")
        return _names_bound_by_module(common_path) | {"st"} | set(dir(builtins))

    def _assert_clean(self, filename):
        common_names = self._common_names()
        path = os.path.join(TABS_DIR, filename)
        file_names = _names_bound_by_module(path)
        available = common_names | file_names

        tree = ast.parse(open(path, encoding="utf-8").read(), path)
        all_problems = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("render_"):
                for lineno, base, attr in _find_undefined_bases(node, available):
                    all_problems.append(f"{filename}:{lineno} `{base}.{attr}` -- "
                                        f"`{base}` is never imported or assigned anywhere reachable")
        assert all_problems == [], all_problems

    def test_workspace_tab(self):
        self._assert_clean("workspace_tab.py")

    def test_library_tab(self):
        self._assert_clean("library_tab.py")

    def test_reader_tab(self):
        self._assert_clean("reader_tab.py")

    def test_scanlate_tab(self):
        self._assert_clean("scanlate_tab.py")

    def test_navigator_tab(self):
        self._assert_clean("navigator_tab.py")

    def test_discover_tab(self):
        self._assert_clean("discover_tab.py")

    def test_settings_tab(self):
        self._assert_clean("settings_tab.py")

    def test_diagnostics_tab(self):
        self._assert_clean("diagnostics_tab.py")


class TestUndefinedNameCheckerItself:
    def test_catches_a_name_never_imported_anywhere(self):
        src = (
            "def render_x():\n"
            "    time.sleep(1)\n"
        )
        tree = ast.parse(src)
        problems = _find_undefined_bases(tree.body[0], available=set())
        assert any(base == "time" for _, base, _ in problems)

    def test_does_not_flag_a_properly_imported_name(self):
        src = (
            "import time\n"
            "def render_x():\n"
            "    time.sleep(1)\n"
        )
        tree = ast.parse(src)
        fn = tree.body[1]
        available = {"time"}
        problems = _find_undefined_bases(fn, available)
        assert problems == []

    def test_lambda_parameter_is_not_flagged(self):
        src = (
            "def render_x():\n"
            "    f = lambda m: m.replace('a', 'b')\n"
        )
        tree = ast.parse(src)
        problems = _find_undefined_bases(tree.body[0], available=set())
        assert problems == []

    def test_comprehension_target_is_not_flagged(self):
        src = (
            "def render_x():\n"
            "    y = [m.upper() for m in items]\n"
        )
        tree = ast.parse(src)
        problems = _find_undefined_bases(tree.body[0], available={"items"})
        assert problems == []

