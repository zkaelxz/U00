"""Make a declared setting read as a given value in a test, without a database.

Patches `stored_settings.read`, which `settings_service.get` and the modules
below services/ both read through, for one key and defers every other key to
the reader that was in place, so patches for several keys stack.
"""
import stored_settings


def patch_setting(monkeypatch, key, value):
    real = stored_settings.read
    monkeypatch.setattr(stored_settings, "read",
                        lambda name, choices_of=None, _real=real:
                        value if name == key else _real(name, choices_of))
