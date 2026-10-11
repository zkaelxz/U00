"""Make a declared setting read as a given value in a test, without a database.

Patches `settings_service.get` for one key and defers every other key to the
reader that was in place, so patches for several keys stack.
"""
from services import settings_service


def patch_setting(monkeypatch, key, value):
    real = settings_service.get
    monkeypatch.setattr(settings_service, "get",
                        lambda name, _real=real: value if name == key else _real(name))
