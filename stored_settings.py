"""Reads one saved setting from the library database as its typed value, for code below services/."""
from lib import settings_schema

_warned_unreadable_keys = set()


def _warn_unreadable_once(key: str) -> None:
    # Once per key so a locked database doesn't flood the log on every read; the
    # exception text is left out because it can carry a path.
    if key in _warned_unreadable_keys:
        return
    _warned_unreadable_keys.add(key)
    import applog
    applog.get_logger().warning("Setting '%s' could not be read; using its default.", key)


def read(key: str, choices_of=None):
    """A declared setting (lib/settings_schema.py) stored in app_settings, as a
    typed value; a missing, unreadable or invalid stored value reads as the
    schema default. A setting with a choices list needs `choices_of` (a
    function returning them), which only services/settings_service.get can
    supply, so it is refused here without one. It is called inside the
    fallback because building the choices can fail too."""
    import db
    setting = settings_schema.BY_KEY[key]
    if setting.store != "db":
        raise ValueError(f"'{key}' is not stored in app_settings.")
    if setting.choices and choices_of is None:
        raise ValueError(f"'{key}' has a choices list; read it with settings_service.get.")
    try:
        return settings_schema.coerce(key, db.get_app_setting(setting.store_key),
                                      choices_of() if choices_of else None)
    except Exception:
        _warn_unreadable_once(key)
        return settings_schema.default_of(setting)
