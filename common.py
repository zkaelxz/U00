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
import background_jobs
import bulk_import
import story_context
import storage
import universe_wiki
import adaptive_style
import line_tools
import emotion
import ui_theme
import translation_guide as tguide
import core as core_module
from core import (
    Line, fmt_ts, lines_to_srt, lines_to_bilingual_srt,
    transcribe_for_timing, split_user_transcript, align_transcript_to_timing,
    chunk_novel_text, extract_audio_from_video, merge_adjacent_short_lines,
)
