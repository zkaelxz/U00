"""Start-up hook for every Python process an e2e run starts: sqlite waits 30 s, not 5 s, for another writer."""
import importlib.machinery
import importlib.util
import os
import sqlite3
import sys

# The specs seed the library from their own `python -c` processes while the API
# server has the same library.db open. On a busy CI runner a commit can hold the
# write lock past sqlite3's 5 s default, and the seed then fails with "database
# is locked". A caller that passes its own timeout keeps it.
_connect = sqlite3.connect


def _connect_patiently(database, *args, **kwargs):
    if not args:
        kwargs.setdefault("timeout", 30.0)
    return _connect(database, *args, **kwargs)


sqlite3.connect = _connect_patiently

# This file hides any sitecustomize the Python install has, so that one runs too.
_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.machinery.PathFinder.find_spec(
    "sitecustomize", [p for p in sys.path if os.path.abspath(p or ".") != _here])
if _spec and _spec.loader:
    _spec.loader.exec_module(importlib.util.module_from_spec(_spec))
