"""Reports which keys-file and environment settings are set, as yes or no and never a value."""
import os

import env_reader
from lib.env_settings import ENV_SETTINGS


def get_status(env_path: str | None = None) -> dict:
    # One read of .env for the whole list; a missing or broken file reads as empty.
    file_env = env_reader.read_env_file(env_path)
    rows = []
    for s in ENV_SETTINGS:
        is_set = bool(os.environ.get(s.name)) or (s.from_file and bool(file_env.get(s.name)))
        rows.append({"name": s.name, "label": s.label, "group": s.group,
                     "from_file": s.from_file, "set": is_set})
    return {"settings": rows}
