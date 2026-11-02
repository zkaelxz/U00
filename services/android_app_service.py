"""The Android shell's update file: which build the server holds and where it is."""

import os

import portable

APK_NAME = "baihe-shell.apk"
VERSION_NAME = "baihe-shell.version"
# Android's versionCode is a signed 32-bit int.
_MAX_VERSION_CODE = 2 ** 31 - 1


def _folder() -> str:
    return os.path.join(portable.data_dir(), "android")


def apk_path() -> str | None:
    path = os.path.join(_folder(), APK_NAME)
    return path if os.path.isfile(path) else None


def latest_version_code() -> int | None:
    """The versionCode written beside the APK when it was put there, or None
    when either file is missing or the number is unusable. The code is read
    from a file, not the APK, because the APK's manifest is binary XML."""
    if apk_path() is None:
        return None
    try:
        with open(os.path.join(_folder(), VERSION_NAME), encoding="ascii") as f:
            code = int(f.read(16).strip())
    except (OSError, ValueError):
        return None
    return code if 0 < code <= _MAX_VERSION_CODE else None
