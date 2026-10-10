"""api/schemas/settings.py -- The declared-settings schema the Settings page builds its forms from.

Metadata only: never a stored value, a secret row or a `.env` key name.
"""

from typing import Any, List, Optional

from pydantic import BaseModel

__all__ = ["SettingsSchemaChoice", "SettingsSchemaRow", "SettingsSchema"]


class SettingsSchemaChoice(BaseModel):
    value: str
    label: str


class SettingsSchemaRow(BaseModel):
    key: str
    type: str
    default: Any = None
    scope: str
    label: str
    help: str = ""
    unit: str = ""
    placeholder: str = ""
    tab: str
    section: str
    custom: bool = False
    dev_only: bool = False
    choices: Optional[List[SettingsSchemaChoice]] = None
    min: Optional[float] = None
    max: Optional[float] = None
    max_len: Optional[int] = None
    multiline: bool = False


class SettingsSchema(BaseModel):
    settings: List[SettingsSchemaRow]
