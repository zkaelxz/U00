"""The Android shell's update file: which build the server holds and where it is."""

import json
import os
import re
import threading
import zipfile

import portable
from lib.errors import ConflictError, DependencyUnavailableError, NotFoundError, ServiceError

# A top-level folder of the data folder: installer/service_layout.APP_STATE_ENTRIES must list it,
# or Setup refuses the data folder once a phone-app build has been fetched.
FOLDER_NAME = "android"
APK_NAME = "baihe-shell.apk"
VERSION_NAME = "baihe-shell.version"
# Android's versionCode is a signed 32-bit int.
_MAX_VERSION_CODE = 2 ** 31 - 1

# The workflow .github/workflows/android-apk.yml keeps these three files on one rolling
# pre-release. A tag that does not start with "v" is never offered as an installer update.
RELEASE_TAG = "android-app"
SHA_NAME = APK_NAME + ".sha256"
MAX_APK_BYTES = 100 * 1024 * 1024
_MAX_TEXT_BYTES = 4096
_MAX_RELEASE_JSON_BYTES = 2_000_000
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_fetch_lock = threading.Lock()


def _folder() -> str:
    return os.path.join(portable.data_dir(), FOLDER_NAME)


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


def _asset_url(assets: dict, name: str) -> str:
    asset = assets.get(name)
    url = asset.get("browser_download_url") if isinstance(asset, dict) else None
    if not isinstance(url, str):
        raise DependencyUnavailableError(
            "The published phone-app build is incomplete, so it was not fetched.")
    return url


def fetch_latest() -> dict:
    """Fetches the build the Android APK workflow published and makes the server offer it.
    Returns {"version_code", "changed"}. The APK is kept only when its SHA-256 matches the
    release's .sha256 and it is a zip; what stops a swapped file from reaching the phone is
    Android refusing an update signed with a different key, not this check."""
    from services import update_service as gh

    if not _fetch_lock.acquire(blocking=False):
        raise ConflictError("A phone-app fetch is already running.")
    try:
        repo = gh.releases_repo()
        try:
            release = json.loads(gh.read_small(
                f"https://api.github.com/repos/{repo}/releases/tags/{RELEASE_TAG}", _MAX_RELEASE_JSON_BYTES,
                accept="application/vnd.github+json"))
        except ServiceError as exc:
            if getattr(exc, "details", None) == {"http_status": 404}:
                raise NotFoundError("No phone-app build has been published yet. On GitHub, open "
                                    "Actions > Android APK > Run workflow.") from None
            raise
        except ValueError:
            raise DependencyUnavailableError("GitHub's answer wasn't readable.") from None
        assets = {a.get("name"): a for a in (release.get("assets") or []) if isinstance(a, dict)} \
            if isinstance(release, dict) else {}
        urls = {name: _asset_url(assets, name) for name in (APK_NAME, SHA_NAME, VERSION_NAME)}
        try:
            code = int(gh.read_small(urls[VERSION_NAME], _MAX_TEXT_BYTES).decode("ascii").strip())
        except (ValueError, UnicodeDecodeError):
            code = 0
        if not 0 < code <= _MAX_VERSION_CODE:
            raise DependencyUnavailableError("The published phone-app version number isn't usable.")
        current = latest_version_code()
        if current is not None and code <= current:
            return {"version_code": current, "changed": False}
        parts = gh.read_small(urls[SHA_NAME], _MAX_TEXT_BYTES).decode("utf-8", "replace").split()
        digest = parts[0].lower() if parts else ""
        if not _HEX64.match(digest):
            raise ConflictError("The published phone-app checksum isn't usable, so nothing was fetched.")
        folder = _folder()
        os.makedirs(folder, exist_ok=True)
        staged = os.path.join(folder, APK_NAME + ".new")
        gh.fetch_verified(urls[APK_NAME], staged, assets[APK_NAME].get("size"), MAX_APK_BYTES,
                          digest, None, None, APK_NAME, lambda _n: None, what="phone app")
        try:
            if not zipfile.is_zipfile(staged):
                raise ConflictError("The fetched phone-app file isn't an APK, so it was deleted.")
            # APK first, version file last: the server offers a build only once both exist.
            os.replace(staged, os.path.join(folder, APK_NAME))
        finally:
            try:
                os.remove(staged)
            except OSError:
                pass
        with open(os.path.join(folder, VERSION_NAME), "w", encoding="ascii") as f:
            f.write(str(code))
        return {"version_code": code, "changed": True}
    finally:
        _fetch_lock.release()
