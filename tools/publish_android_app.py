"""Builds the Android app with the next version number and puts it where the server offers it."""

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import portable  # noqa: E402
from services import android_app_service as offered  # noqa: E402


def main() -> int:
    next_code = (offered.latest_version_code() or 0) + 1
    android = os.path.join(ROOT, "android")
    gradlew = "gradlew.bat" if os.name == "nt" else "./gradlew"
    built = subprocess.run([gradlew, "assembleDebug", f"-PbaiheVersionCode={next_code}"], cwd=android)
    if built.returncode != 0:
        return built.returncode
    apk = os.path.join(android, "app", "build", "outputs", "apk", "debug", "app-debug.apk")
    folder = os.path.join(portable.data_dir(), "android")
    os.makedirs(folder, exist_ok=True)
    # Replace the APK atomically and write the version file last: the server offers a build only
    # once both exist, and a phone downloading mid-copy must never get half a file.
    target = os.path.join(folder, offered.APK_NAME)
    shutil.copyfile(apk, target + ".part")
    os.replace(target + ".part", target)
    with open(os.path.join(folder, offered.VERSION_NAME), "w", encoding="ascii") as f:
        f.write(str(next_code))
    print(f"Offering app build {next_code} from {folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
