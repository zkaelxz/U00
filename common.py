"""
common.py -- shared imports and module-level names used across all
tabs/*.py modules. Each tab file does `from common import *` instead
of repeating the same long import list.
"""

import os
import re
import json
import zipfile
import io

import streamlit as st
import pandas as pd

import db
import translate_engines
import dub as dub_module
import dictionary
import reader as reader_module
import vocab_export
import qa
import app_help
import background_jobs
import bulk_import
import story_context
import storage
import universe_wiki
import adaptive_style
import line_tools
import debug_view
import emotion
import ui_theme
import translation_guide as tguide
import core as core_module
from core import (
    Line, fmt_ts, lines_to_srt, lines_to_bilingual_srt,
    transcribe_for_timing, split_user_transcript, align_transcript_to_timing,
    chunk_novel_text, extract_audio_from_video, merge_adjacent_short_lines,
)


def get_active_profile_id() -> int:
    """The household profile (Step 26e) the current browser session is
    acting as, for reading progress/history and personal notes -- Jellyfin-
    style, not an accounts/login system: this app has no auth of its own,
    so a profile is "which household member is this" picked from a list,
    not a password-protected identity. Session-state-scoped like
    active_drama_id, so each concurrent session/tab picks its own profile
    independently. The Settings sidebar's picker is what actually sets
    this when someone switches profiles; this is a read-with-fallback so
    every other tab has something sane even if that picker never ran (a
    script/test context, or the sidebar hit its own try/except in app.py).
    A single-profile household never has to think about any of this."""
    active = st.session_state.get("active_profile_id")
    if active is not None:
        return active
    profiles = db.list_profiles()
    if not profiles:
        db.init_db()  # creates the default profile (Step 26e's migration)
        profiles = db.list_profiles()
    st.session_state.active_profile_id = profiles[0]["id"]
    return st.session_state.active_profile_id


def _pull_canonical_settings_value(settings_key: str, widget_key: str):
    """
    The actual fix inside synced_api_key_input(), split out as a pure
    session_state operation so it's testable without a real Streamlit
    script run (st.text_input() itself can't be meaningfully unit
    tested outside one -- called "bare", it always returns "" regardless
    of session_state, which is why this logic is kept separate from the
    widget call it feeds).

    Streamlit only applies a widget's `value=` argument on its very
    first render in a given browser session; a widget with its own
    explicit `key` freezes to whatever it last held after that, no
    matter what a *different* widget for "the same" logical value does
    later. This was a real, previously-unnoticed bug across this app:
    typing an API key into the Settings sidebar never reached any
    per-tab field duplicating it (Workspace's HF token box, Reader's
    Q&A/Story tools keys, Scanlate's engine key, Discover/Navigator's
    lookup keys, and others), even though the underlying
    settings_<name> value genuinely updated -- each of those fields is
    its own separately-keyed widget, and after its first render, a
    `value=` referencing settings_<name> is silently ignored on every
    later rerun.

    Pulls the canonical value into `widget_key`'s own session_state
    entry only when that field is still blank -- one-directional, not a
    hard lock-step, so deliberately clearing one field isn't
    immediately overwritten again on the very next rerun.
    """
    settings_state_key = f"settings_{settings_key}"
    canonical = st.session_state.get(settings_state_key, "")
    if canonical and not st.session_state.get(widget_key):
        st.session_state[widget_key] = canonical
    return settings_state_key


def synced_api_key_input(label, settings_key, widget_key, **kwargs):
    """
    A text_input for an API key that also appears elsewhere -- the
    Settings sidebar, or another tab's own copy of the same field --
    kept in sync with it both ways. See
    _pull_canonical_settings_value()'s docstring for the bug this fixes.
    """
    settings_state_key = _pull_canonical_settings_value(settings_key, widget_key)
    kwargs.setdefault("type", "password")
    value = st.text_input(label, key=widget_key, **kwargs)
    if value:
        st.session_state[settings_state_key] = value
    return value
