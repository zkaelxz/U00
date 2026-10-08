# Starting a project like this one

A retrospective: what to set up before the first feature if a project like Baihe were started from scratch,
written for an owner directing AI coding sessions without a programming background. It records what this
repo learned the hard way; `CLAUDE.md` holds the current rules and `engineering-standards.md` the review policy.

## What cost the most here

These are facts from this repo's history, not guesses:

- **Files grew unchecked.** `db.py` reached about 6,200 lines and 24 Python files passed 40 KB before a size guard
  existed (`tests/test_static_analysis.py::TestModuleSize`). The guard arrived late with an allowlist of existing offenders, so
  it froze the problem instead of preventing it. Large files are unreadable for a small-context model and slow to review.
- **Parallel sessions collided.** Many sessions editing the same big files meant every merge made other PRs conflict, and
  a PR with conflicts gets no CI at all.
- **Real-model problems only showed on the owner's machine.** The test suite is mocked, so GPU, model-loader and
  library-version breakage (`av` 19, OCR on transformers 5.x, Windows text decoding) surfaced as user-visible errors.
- **Installs were not pinned.** A plain `pip install` pulled a newer `av` that broke transcription; `constraints.txt` existed but the
  Diagnostics install path ignored it.
- **Features were added before they were tested on real content,** then removed (NLLB, TADA, Chatterbox, GPT-SoVITS, MOSS,
  Edge TTS, Piper, F5-TTS). Each removal cost a migration, a pattern in `REMOVED_ENGINES`, and review time.
- **Windows specifics were found one bug at a time:** subprocess output decoded with the system code page returns `None` on
  CJK text, screenshots were committed to `tmp/`, and so on.

## Day one, before any feature

Do these first. Each is cheap at the start and expensive later.

1. **Write `CLAUDE.md` (or `AGENTS.md`) before code.** Layout, how to run, how to test, and the rules. Keep it under about 100 lines and
   add a rule every time a real bug teaches one.
2. **Fix the layers and enforce them with a test.** UI calls API, API calls services, services call domain modules, domain modules
   call the database. Imports only go downward. A test fails if a router imports the database directly.
3. **Set a hard file-size limit and enforce it with a failing test from the first commit.** About 40 KB or 600 lines for source files, no
   allowlist. When a file nears the limit, split it then. Apply the same limit to frontend files and new test files.
4. **Make the database a package from the start** (`db/` with one module per domain and a front door in `__init__`), not one file.
   Schema changes are additive (`ALTER TABLE ... ADD COLUMN`), and a guard test lists every migrated column.
5. **Set up CI on the first commit:** backend tests, frontend type-check and unit tests, and browser end-to-end tests. Make green CI the only
   merge gate.
6. **Test in two layers.** Fast mocked tests for every change, plus a small real-model smoke test (one short clip, one image, one
   translation) that anyone can run on demand on the target machine. Mocks cannot catch GPU or driver problems.
7. **Pin dependencies and make every install honour the pin.** One `constraints.txt`; every install command, including in-app ones, passes
   `-c constraints.txt`. Pin native-heavy libraries (`torch`, `transformers`, `av`, `faster-whisper`) and upgrade them one at a time.
8. **Decide the target platform and test on it early.** For Windows: pass `encoding="utf-8"` to every subprocess that reads text, test
   with CJK file names and paths containing spaces, and handle long paths.
9. **Security defaults on from day one:** API keys in headers only, error text passed through a redactor, a timeout on every outbound
   HTTP call, one permission declaration per route, local-only access unless explicitly opened. Add the tests that enforce them.
10. **Protect user data:** automatic backups before migrations and upgrades, field-level writes so one job can't overwrite another's
    edits, and no destructive action without explicit confirmation.
11. **Repo hygiene:** a `.gitignore` for `tmp/`, build output and local databases; screenshots go on the PR, not in the repo; no
    secrets in files.

## Running AI sessions

- **One task per branch, small PRs.** A PR you can't review in one sitting is too big. Split by domain.
- **Keep tasks file-disjoint when running in parallel.** Two sessions editing the same file will conflict. For a large shared file
  (a database module, an allowlist), freeze it, do the change alone, then reopen.
- **Merge in a fixed order** and update the remaining branches after each merge. Never merge two big PRs at once.
- **Use the stronger model for risky work:** security, auth, concurrency, data integrity and large refactors. Use the cheaper one for
  routine changes.
- **Require evidence for "done":** the exact commands run and their pass counts, plus anything left unverified. Treat "tests pass"
  without counts as unfinished.
- **Write decisions down once, briefly.** A short decision record beats re-explaining. Keep docs indexed from one front page and
  label any number copied into a doc as a snapshot.
- **Re-check old notes against the code** before acting on them. Docs drift; code and git are the source of truth.

## Choosing features and models

- **Test a model or engine on your own content before adding it.** Build a small benchmark set from lines you have already reviewed, so
  the references cost nothing, and compare candidates on quality, cost and speed.
- **Ship experiments off by default and clearly labelled,** with a plan to remove them. Support only what you test.
- **Prefer one good option per job** over many half-tested ones. Every extra engine adds migration, documentation and review work.
- **Know your hardware limits first** (VRAM and RAM decide which models are usable) and write them where the model choices are made.

## First-week checklist

1. `CLAUDE.md` with layout, commands and rules.
2. Layer test, file-size test, timeout test, permission test (all failing-by-default).
3. CI running on every PR.
4. `db/` package with a migration guard.
5. `constraints.txt` and an install path that uses it.
6. A real-model smoke test script, even if it only does one thing.
7. `.gitignore`, backups and a restore command.
8. A benchmark folder with ten reviewed lines in each language you care about.

## Kickoff prompt to give an AI at the start

> Set up this project with the day-one checklist in `docs/starting-a-project.md`: layering and size limits enforced by tests, CI,
> a database package with a migration guard, pinned installs, and a mocked plus a real-model smoke test. Show me the plan and wait
> for approval before writing features.

## What this repo has and lacks today (snapshot, 2026-10)

- In place: `CLAUDE.md`, layering and route-permission tests, outbound-timeout test, a size guard with an allowlist, `constraints.txt`, CI.
- In progress or missing: the allowlisted large files and `db.py` split, a frontend size guard, a real-model smoke test, and pinned installs on
  every path. Track these in `STATUS.md`.
