"""app_tools.py -- the app's own folder for helper programs it downloads (ffmpeg), and putting it on this process's PATH."""

import os

import portable

FOLDER_NAME = "tools"
FFMPEG_FOLDER = "ffmpeg"
_FFMPEG_PROGRAMS = ("ffmpeg.exe", "ffprobe.exe")


def tools_folder() -> str:
    return os.path.join(portable.data_dir(), FOLDER_NAME)


def ffmpeg_folder() -> str:
    return os.path.join(tools_folder(), FFMPEG_FOLDER)


def app_ffmpeg_present() -> bool:
    folder = ffmpeg_folder()
    return all(os.path.isfile(os.path.join(folder, name)) for name in _FFMPEG_PROGRAMS)


def activate() -> bool:
    """Adds the downloaded ffmpeg folder to the END of this process's PATH, so
    every `ffmpeg` call here, in job child processes and in yt-dlp finds it
    without code changes, while an ffmpeg the user installed themselves still
    wins. Only this process's environment changes, never the system PATH."""
    if not app_ffmpeg_present():
        return False
    folder = ffmpeg_folder()
    parts = os.environ.get("PATH", "").split(os.pathsep)
    if not any(os.path.normcase(p) == os.path.normcase(folder) for p in parts):
        os.environ["PATH"] = os.pathsep.join([p for p in parts if p] + [folder])
    return True
