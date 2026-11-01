"""
lib/file_manager.py -- opening a folder in this PC's file manager.

Callers pass a folder they resolved server side, never a path from a client:
the folder opens on the PC's own screen, so only PC-only routes reach this.
"""

import os
import subprocess
import sys


def open_folder(folder: str):
    """Raises OSError when the PC has no file manager to open it with."""
    if sys.platform.startswith("win"):
        getattr(os, "startfile")(folder)  # Windows-only; the owner's own folder, on the PC
    else:
        # Popen without a shell; the file manager outlives this call.
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", folder],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
