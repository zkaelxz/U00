"""
tests/conftest.py -- shared pytest fixtures.

Key fixture: `isolated_db` redirects db.py's library path to a fresh
temp directory for each test, so running the test suite never reads,
writes, or deletes anything in your actual library.
"""

import sys
import os
import shutil
import tempfile
import pytest

# Make the project root importable when running `pytest` from anywhere
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db


@pytest.fixture
def isolated_db():
    """Redirects db.py to a fresh temp directory for the duration of
    one test, then cleans up afterward. Use this fixture in any test
    that touches the database."""
    temp_dir = tempfile.mkdtemp(prefix="baihe_test_")
    db.configure_library_dir(temp_dir)
    db.init_db()
    yield db
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def tmp_path_str():
    """A plain temp directory as a string path, cleaned up afterward.
    (pytest has a built-in tmp_path fixture, but it yields a pathlib
    Path; several functions here take str paths, so this avoids
    scattering str() conversions through the tests.)"""
    d = tempfile.mkdtemp(prefix="baihe_tmp_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def sample_lines():
    """A small, representative set of Line objects for testing
    alignment/merging/translation logic without needing real audio."""
    from core import Line
    return [
        Line(idx=0, start=0.0, end=0.5, zh="你", en="You", speaker="A"),
        Line(idx=1, start=0.6, end=1.0, zh="好", en="good", speaker="A"),
        Line(idx=2, start=1.1, end=3.0, zh="今天天气不错", en="The weather is nice today", speaker="A"),
        Line(idx=3, start=5.0, end=5.3, zh="嗯", en="Mm", speaker="B"),
    ]


@pytest.fixture
def sample_drama_meta():
    return {"title_en": "Test Drama", "title_zh": "测试剧", "author": "Test Author"}
