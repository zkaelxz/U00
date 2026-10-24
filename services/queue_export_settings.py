"""
services/queue_export_settings.py -- the Export half of the Queue card's
"Saved settings for this step": the fields one queued item may change, what the
runner accepts for them, and the file the runner writes.
"""

import os
import tempfile
from typing import Optional

import db
import subtitle_formats
from lib.errors import ConflictError, InvalidInputError, UnsupportedOperationError
from services import artifact_service, export_service

EXPORT_KEYS = ("format", "language")
_FORMATS = {"srt": "SRT", "vtt": "VTT", "lrc": "LRC (lyrics)"}
# The export stage's own Language choices, keyed by the field generate_subtitle_text takes.
_LANGUAGES = {"en": "English", "zh": "Source language", "bilingual": "Both"}
_LABELS = {"format": "Format", "language": "Language"}
_OPTIONS = {"format": _FORMATS, "language": _LANGUAGES}
_DEFAULTS = {"format": "srt", "language": "en"}
_NEEDS_EDIT = "Needs permission to edit lines."


def export_fields(holds_edit: bool = True) -> list:
    return [{"key": key, "label": _LABELS[key], "kind": "select", "value": _DEFAULTS[key],
             "options": [{"value": v, "label": label} for v, label in _OPTIONS[key].items()],
             "min": None, "max": None, "locked": None if holds_edit else _NEEDS_EDIT}
            for key in EXPORT_KEYS]


def check_export_settings(overrides) -> dict:
    if overrides is None:
        return {}
    if not isinstance(overrides, dict):
        raise InvalidInputError("Settings must be an object.")
    unknown = sorted(set(overrides) - set(EXPORT_KEYS))
    if unknown:
        raise InvalidInputError(f"Unknown setting {unknown[0]!r}.")
    given = {k: v for k, v in overrides.items() if v is not None}
    for key, value in given.items():
        if value not in _OPTIONS[key]:
            raise InvalidInputError(f"Unknown {_LABELS[key].lower()}.")
    return given


def changed_rows(settings: dict) -> list:
    return [{"label": _LABELS[k], "value": _OPTIONS[k].get(v, str(v)), "engine": None}
            for k, v in settings.items() if k in _OPTIONS]


def run_export(drama: dict, settings: Optional[dict] = None) -> None:
    """Writes the title's subtitle file to its subtitle download: English SRT by
    default (the stage's own choices are per browser, so the server has none saved),
    or what this item chose."""
    from jobs import job_launch
    settings = check_export_settings(settings)
    fmt, field = settings.get("format", "srt"), settings.get("language", "en")
    if not job_launch.enter_maintenance():
        raise ConflictError("A restore or reset is running, so the export can't run now.")
    try:
        # The generator words its own refusal for a source-language export.
        if field != "zh" and not any((ln.get("en") or "").strip() for ln in db.load_lines(drama["id"])):
            raise UnsupportedOperationError("There are no English lines to export yet.")
        text = export_service.generate_subtitle_text(drama["id"], fmt, field)
        name = f"subtitles_{drama['id']}.{fmt}"
        path = artifact_service.output_path(drama["id"], "subtitle", name)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".queue-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(subtitle_formats.srt_file_text(text) if fmt == "srt" else text)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        artifact_service.set_download_language(
            drama["id"], "subtitle", name, export_service.field_language(drama, field))
    finally:
        job_launch.exit_maintenance()
