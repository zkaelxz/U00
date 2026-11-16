"""
services/assistant_pytest_guard.py -- a pytest plugin loaded (`-p`) only
by the maintenance assistant's run_tests tool. Before any test
module is imported it points the db package at a throwaway library and
settings_service at an empty .env, so a test that forgot `isolated_db`
can't write the user's real library or read a real key. Refuses to load
outside that run.
"""

import os
import shutil
import tempfile

if os.environ.get("BAIHE_ASSISTANT_TEST_RUN") != "1":
    raise RuntimeError("assistant_pytest_guard is only for the assistant's test runs.")


_scratch = None


def pytest_configure(config):
    import db
    from services import settings_service

    global _scratch
    _scratch = scratch = tempfile.mkdtemp(prefix="baihe_assistant_tests_")
    library = os.path.join(scratch, "library")
    os.makedirs(library)
    db.configure_library_dir(library)
    empty_env = os.path.join(scratch, ".env")
    open(empty_env, "w").close()
    settings_service.default_env_path = lambda: empty_env


def pytest_unconfigure(config):
    # A hard kill skips this; storage's legacy sweep removes the folder next start.
    if _scratch:
        shutil.rmtree(_scratch, ignore_errors=True)
