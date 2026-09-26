"""
sources/adapters -- real site adapters, one module per site. Each module
registers its adapter with @registry.register on import; list it in
BUILTIN below so the app loads it.
"""

import importlib

BUILTIN = ["manhuagui", "bilibili", "52shuku", "xbanxia", "bilibili_manga",
          "toonkor", "guazimanhua", "miaoqumh", "baozimh", "kuaikan", "manhuaku",
          "zerosumonline", "mangaz"]


def load_all():
    for name in BUILTIN:
        importlib.import_module(f"{__name__}.{name}")
