"""tools/pr_lane.py: which merge lane a branch takes. A wrong `clear` lets two changes meet untested, so each rule
of docs/testing-and-ci.md "Merge flow" is pinned against a throwaway git repository."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import ci_changes  # noqa: E402
import pr_lane  # noqa: E402

IDENTITY = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}

# init_db is lines 8 to 12: the tests below edit inside it, next to it and after it.
DB_INIT = '''"""db"""


def other():
    return 1


def init_db():
    steps = []
    steps.append("a")
    steps.append("b")
    return steps


def later():
    return 2
'''


def git(root, *args):
    done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=60, env={**os.environ, **IDENTITY})
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def commit(root, files, message="change"):
    for rel, text in files.items():
        path = Path(root, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)


def new_repo(path):
    path.mkdir()
    git(path, "init", "-q")
    git(path, "symbolic-ref", "HEAD", "refs/heads/main")
    commit(path, {"a.py": "a = 1\n", "shared.py": "x = 1\n", "db/__init__.py": DB_INIT,
                  "services/migrate.py": 'conn.execute("ALTER TABLE a ADD COLUMN b TEXT")\nx = 1\n'}, "seed")
    return path


@pytest.fixture
def repo(tmp_path):
    return new_repo(tmp_path / "repo")


def lane(repo, pr, base=None):
    """The verdict for a branch that changed `pr` while main changed `base`, both cut from the same commit."""
    git(repo, "checkout", "-q", "-b", "feature")
    commit(repo, pr, "pr")
    git(repo, "checkout", "-q", "main")
    if base:
        commit(repo, base, "base")
    return pr_lane.decide(repo, "main", "feature")


def run(repo, *argv):
    return pr_lane.main(["--no-fetch", "--base", "main", *argv], root=repo)


def test_files_nobody_else_changed_take_the_clear_lane(repo):
    v = lane(repo, {"a.py": "a = 2\n"}, {"b.py": "b = 1\n"})
    assert v.clear and v.shared == [] and v.always == {}


def test_a_file_changed_on_both_sides_takes_the_overlap_lane(repo):
    v = lane(repo, {"shared.py": "x = 2\n", "a.py": "a = 2\n"}, {"shared.py": "x = 3\n"})
    assert not v.clear and v.shared == ["shared.py"]


def test_a_branch_up_to_date_with_the_base_shares_nothing(repo):
    v = lane(repo, {"shared.py": "x = 2\n"})
    assert v.clear and v.base_files == 0


def test_only_what_the_base_merged_after_the_last_base_merge_counts(repo):
    git(repo, "checkout", "-q", "-b", "feature")
    git(repo, "checkout", "-q", "main")
    commit(repo, {"shared.py": "x = 5\n"})
    git(repo, "checkout", "-q", "feature")
    git(repo, "merge", "-q", "--no-edit", "main")  # the branch already holds that change, and its green run tested it
    commit(repo, {"shared.py": "x = 6\n"})
    git(repo, "checkout", "-q", "main")
    commit(repo, {"b.py": "b = 1\n"})
    v = pr_lane.decide(repo, "main", "feature")
    assert v.clear and v.shared == []


def test_a_rename_counts_as_a_change_to_the_old_path(repo):
    git(repo, "checkout", "-q", "-b", "feature")
    git(repo, "mv", "shared.py", "renamed.py")
    git(repo, "commit", "-q", "-m", "move")
    git(repo, "checkout", "-q", "main")
    commit(repo, {"shared.py": "x = 9\n"})
    assert pr_lane.decide(repo, "main", "feature").shared == ["shared.py"]


def test_user_guide_files_do_not_count_as_shared(repo):
    guide = "docs/user-guide-subtitles.md"
    v = lane(repo, {guide: "pr\n"}, {guide: "base\n"})
    assert v.clear and v.shared == [] and v.exempt == [guide]
    assert f"  Not counted as shared (user guide files): {guide}" in v.lines()


def test_a_user_guide_file_does_not_hide_a_code_file_both_sides_changed(repo):
    v = lane(repo, {"docs/user-guide.md": "pr\n", "shared.py": "x = 2\n"},
             {"docs/user-guide.md": "base\n", "shared.py": "x = 3\n"})
    assert not v.clear and v.shared == ["shared.py"] and v.exempt == ["docs/user-guide.md"]


@pytest.mark.parametrize("path", ["docs/archive/user-guide-old.md", "docs/guides/user-guide.md",
                                  "docs/user-guide.txt", "docs/testing-and-ci.md", "user-guide.md"])
def test_only_the_user_guide_files_directly_in_docs_are_exempt(repo, path):
    assert lane(repo, {path: "pr\n"}, {path: "base\n"}).shared == [path]


@pytest.mark.parametrize("path,why", [
    (".github/workflows/tests.yml", "CI file"),
    ("tools/ci_changes.py", "CI file"),
    ("tools/ci_tested_tree.py", "CI file"),
    ("tests/conftest.py", "CI file"),
    ("requirements-core.txt", "CI file"),
    ("constraints.txt", "CI file"),
    ("frontend/package-lock.json", "CI file"),
    ("api/schemas/common.py", "API model"),
    ("api/jobs_schemas.py", "API model"),
    ("frontend/src/api/generated/types.ts", "API model"),
    ("installer/wheels.lock.txt", "dependency file"),
    ("installer/caddy/go.mod", "dependency file"),
    ("installer/caddy/go.sum", "dependency file"),
    ("frontend/openapi-gen/package.json", "dependency file"),
    ("frontend/openapi-gen/package-lock.json", "dependency file"),
    ("android/build.gradle.kts", "dependency file"),
    ("android/app/build.gradle.kts", "dependency file"),
    ("android/gradle.properties", "dependency file"),
    ("android/gradle/wrapper/gradle-wrapper.properties", "dependency file"),
    ("android/gradlew", "dependency file"),
    ("db/migrations.py", "database schema"),
    ("db/schema.py", "database schema"),
    ("db/schema_system.py", "database schema"),
    ("sources/store.py", "database schema"),
])
def test_the_files_kae_guards_always_take_the_overlap_lane(repo, path, why):
    assert pr_lane.path_kind(path) == why
    v = lane(repo, {path: "changed\n"})
    assert not v.clear and v.always == {path: why}
    assert f"  Always the overlap lane: {path} ({why})" in v.lines()


@pytest.mark.parametrize("path", [
    "api/routers/jobs_routes.py", "api/routers/x_schemas.py", "services/x.py", "docs/user-guide.md",
    "installer/service_lock.py", "installer/setup_lock.py", "tools/pr_lane.py", "tests/test_db.py", "db/__init__.py",
    "android/app/src/main/java/App.kt", "frontend/src/App.tsx",
])
def test_other_files_are_not_guarded_by_their_path(path):
    assert pr_lane.path_kind(path) is None


def test_every_file_that_makes_ci_run_everything_takes_the_overlap_lane():
    paths = [*ci_changes.EVERYTHING_FILES, *(p + "x" for p in ci_changes.EVERYTHING_PREFIXES),
             *(p.replace("*", "x") for p in ci_changes.EVERYTHING_PATTERNS)]
    assert paths and all(pr_lane.path_kind(p) == "CI file" for p in paths)


def _db(old, new):
    assert old in DB_INIT
    return DB_INIT.replace(old, new)


@pytest.mark.parametrize("edit,counts", [
    (('    return 1\n', '    return 11\n'), False),   # another function, before init_db
    (('    return 2\n', '    return 22\n'), False),   # another function, after it
    (('    steps.append("a")\n', '    steps.append("a2")\n'), True),   # a step changed
    (('    steps.append("b")\n', ''), True),   # a step deleted
    (('    steps.append("b")\n', '    steps.append("b")\n    steps.append("c")\n'), True),   # a step added
    (('    return steps\n', '    return steps\n\n\ndef added():\n    return 3\n'), False),   # a function next to it
])
def test_in_db_init_only_an_edit_to_init_db_counts(repo, edit, counts):
    v = lane(repo, {"db/__init__.py": _db(*edit)})
    assert (v.always == {"db/__init__.py": "database schema"}) is counts


def test_an_unreadable_db_init_is_assumed_to_count(repo):
    v = lane(repo, {"db/__init__.py": "def other(:\n"})
    assert v.always == {"db/__init__.py": "database schema"}


@pytest.mark.parametrize("path,text,counts", [
    ("services/x.py", 'conn.execute("ALTER TABLE a ADD COLUMN b TEXT")\n', True),
    ("device_tokens.py", "CREATE TABLE IF NOT EXISTS t (id INTEGER)\n", True),
    ("services/x.py", "db._safe_alter(conn, sql)\n", True),
    ("services/x.py", 'sql = "create unique index i on t (c)"\n', True),
    ("services/x.py", "x = 1\n", False),
    ("tests/test_x.py", 'conn.execute("ALTER TABLE a ADD COLUMN b TEXT")\n', False),
    ("docs/notes.md", "ALTER TABLE a ADD COLUMN b TEXT\n", False),
])
def test_a_table_statement_added_in_code_is_a_schema_change(repo, path, text, counts):
    v = lane(repo, {path: text})
    assert (v.always == {path: "database schema"}) is counts


def test_removing_a_table_statement_is_a_schema_change_too(repo):
    v = lane(repo, {"services/migrate.py": "x = 1\n"})
    assert v.always == {"services/migrate.py": "database schema"}


def test_editing_the_tool_itself_is_not_a_schema_change():
    lines = Path(pr_lane.__file__).read_text(encoding="utf-8").splitlines()
    assert not [line for line in lines if pr_lane.TABLE_STATEMENT.search(line)]


def test_a_long_list_is_cut_and_counted():
    assert pr_lane._listing([f"f{i}" for i in range(15)]).endswith("f11, and 3 more")
    assert pr_lane._listing(["a", "b"]) == "a, b"


def test_the_report_leads_with_the_verdict_and_the_exit_status_follows_it(repo, capsys):
    git(repo, "checkout", "-q", "-b", "feature")
    commit(repo, {"a.py": "a = 2\n"})
    assert run(repo, "--head", "feature") == 0
    first, summary = capsys.readouterr().out.splitlines()[:2]
    assert first == "clear" and "last merged the base at" in summary
    git(repo, "checkout", "-q", "-b", "other", "main")
    commit(repo, {".github/workflows/tests.yml": "x\n"})
    assert run(repo, "--head", "other") == 1
    assert capsys.readouterr().out.splitlines()[0] == "overlap"


def test_an_unknown_head_cannot_be_decided_and_is_not_cleared(repo, capsys):
    assert run(repo, "--head", "nope") == 2
    assert capsys.readouterr().out.startswith("overlap\n  Could not decide")


def test_a_branch_with_no_commit_in_common_cannot_be_decided(repo, capsys):
    git(repo, "checkout", "-q", "--orphan", "island")
    git(repo, "rm", "-rqf", ".")
    commit(repo, {"z.py": "1\n"})
    assert run(repo, "--head", "island") == 2
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "overlap" and "share no commit" in out[1]


def test_a_criss_cross_history_cannot_be_decided(repo, capsys):
    git(repo, "checkout", "-q", "-b", "feature")
    commit(repo, {"f.py": "1\n"})
    git(repo, "checkout", "-q", "main")
    commit(repo, {"m.py": "1\n"})
    git(repo, "checkout", "-q", "feature")
    git(repo, "merge", "-q", "--no-edit", "main")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-edit", "feature~1")
    assert run(repo, "--head", "feature") == 2
    assert "more than one merge base" in capsys.readouterr().out


def test_the_base_is_fetched_first_so_a_stale_clone_cannot_clear_a_pr(tmp_path, capsys):
    origin = tmp_path / "origin.git"
    origin.mkdir()
    git(origin, "init", "-q", "--bare")
    git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
    work = new_repo(tmp_path / "work")
    git(work, "remote", "add", "origin", str(origin))
    git(work, "push", "-q", "origin", "main")
    git(work, "checkout", "-q", "-b", "feature")
    commit(work, {"shared.py": "x = 2\n"})
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(origin), str(other))
    commit(other, {"shared.py": "x = 3\n"})
    git(other, "push", "-q", "origin", "main")
    # This clone has not heard of the merge yet: without the fetch the PR would read as clear.
    assert pr_lane.main(["--no-fetch", "--base", "origin/main", "--head", "feature"], root=work) == 0
    capsys.readouterr()
    assert pr_lane.main(["--base", "origin/main", "--head", "feature"], root=work) == 1
    assert "Shared with what the base merged since: shared.py" in capsys.readouterr().out
