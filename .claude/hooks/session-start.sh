#!/bin/bash
# Installs what the test suite needs when a Claude Code on the web session
# starts, so `python run_tests.py` works immediately. Does nothing on a
# local machine (your own venv is left alone).
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# Use the known-good version pins once they exist (roadmap Step 1c).
CONSTRAINTS=()
if [ -f constraints.lock.txt ]; then
  CONSTRAINTS=(-c constraints.lock.txt)
elif [ -f constraints.txt ]; then
  CONSTRAINTS=(-c constraints.txt)
fi

# Core app + test runner, plus the light optional libraries whose tests
# would otherwise be skipped. Heavy/GPU extras (torch, pyannote, whisper,
# omnivoice, paddleocr) are deliberately left out -- tests mock them.
# Cloud containers' system Python can be marked "externally managed";
# this is a throwaway container, so installing into it directly is fine.
# --use-pep517: jieba ships only an sdist whose legacy `setup.py
# bdist_wheel` build fails against the container's system setuptools,
# which aborted the whole install (no pytest, no fastapi).
PIP_BREAK_SYSTEM_PACKAGES=1 python3 -m pip install --quiet --disable-pip-version-check \
  --use-pep517 \
  "${CONSTRAINTS[@]}" \
  -r requirements-core.txt \
  pytest pytest-xdist jieba pypinyin opencc-python-reimplemented \
  opencv-python-headless pytesseract numpy pillow

# The app's modules are imported from the repo root in tests.
echo 'export PYTHONPATH="$CLAUDE_PROJECT_DIR${PYTHONPATH:+:$PYTHONPATH}"' >> "$CLAUDE_ENV_FILE"
