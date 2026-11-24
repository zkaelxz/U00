"""lib/no_window.py -- the creation flag that keeps a child console program from opening a window."""

import subprocess

# Pass as `creationflags=` on every subprocess call the running app makes.
# The app runs windowless (pythonw) on a desktop, so a console child such as
# ffmpeg, nvidia-smi or taskkill gets a console of its own and flashes a
# cmd window. 0 where the flag doesn't exist, which Popen accepts everywhere.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
