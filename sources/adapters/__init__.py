"""
sources/adapters -- real site adapters, one module per site. Each module
registers its adapter with @registry.register on import; list it in
BUILTIN below so the app loads it.
"""

import importlib

BUILTIN = ["manhuagui", "bilibili"]


def load_all():
    for name in BUILTIN:
        importlib.import_module(f"{__name__}.{name}")
