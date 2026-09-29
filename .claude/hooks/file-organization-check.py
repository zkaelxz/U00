#!/usr/bin/env python3
"""PostToolUse hook (Write): remind the session to add a new module to FILE_ORGANIZATION.md.

Root CLAUDE.md requires a FILE_ORGANIZATION.md entry in the same PR for every new
top-level module, tabs/*.py, services/*.py or api/routers/*.py file. This only warns
(adds context for Claude); it never blocks, and it stays silent on any error.
"""
import json
import os
import sys


def main():
    try:
        data = json.load(sys.stdin)
        path = (data.get("tool_input") or {}).get("file_path") or ""
        root = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd()
        rel = os.path.relpath(os.path.abspath(path), os.path.abspath(root)).replace(os.sep, "/")
        parts = rel.split("/")
        watched = (
            (len(parts) == 1 and rel.endswith(".py"))
            or (len(parts) == 2 and parts[0] in ("tabs", "services") and rel.endswith(".py"))
            or (len(parts) == 3 and parts[:2] == ["api", "routers"] and rel.endswith(".py"))
        )
        if not watched or parts[-1] == "__init__.py" or parts[-1].startswith("test_"):
            return
        with open(os.path.join(root, "FILE_ORGANIZATION.md"), encoding="utf-8") as fh:
            if parts[-1] in fh.read():
                return
        msg = (f"{rel} is not listed in FILE_ORGANIZATION.md. Root CLAUDE.md requires adding it "
               "to the tree in the right subsystem group in this same change.")
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                                 "additionalContext": msg}}))
    except Exception:
        return


main()
