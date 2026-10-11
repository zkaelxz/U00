"""The declared settings (lib/settings_schema.py) as the metadata the Settings page renders.

Never carries a stored value: the page reads values from the overview.
"""
from lib import settings_schema
from services import maintenance_assistant_service, settings_service


def _choices(name):
    # The stored value doubles as the label; the page prettifies by `name`.
    return [{"value": v, "label": v} for v in settings_service._SCHEMA_CHOICES[name]()]


def get_schema() -> dict:
    developer_mode = maintenance_assistant_service.developer_mode_enabled()
    rows = []
    for s in settings_schema.SETTINGS:
        # Keys in .env are secrets and have their own routes; a row with no tab
        # is state the app keeps for itself and has no field.
        if s.secret or s.store != "db" or not s.tab or (s.dev_only and not developer_mode):
            continue
        rows.append({
            "key": s.key, "type": s.type, "default": settings_schema.default_of(s),
            "scope": s.scope, "label": s.label, "help": s.help, "unit": s.unit,
            "placeholder": s.placeholder, "tab": s.tab, "section": s.section,
            "custom": s.custom, "dev_only": s.dev_only,
            "choices": _choices(s.choices) if s.choices else None,
            "min": s.min, "max": s.max, "max_len": s.max_len, "multiline": s.multiline,
        })
    return {"settings": rows}
