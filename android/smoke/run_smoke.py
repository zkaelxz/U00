"""Installs the release APK on a running emulator and walks the screens a broken build has failed on before.

Needs `adb` on PATH and one emulator (a Google APIs image, not a Play Store one: Quick read's reader is not
exported, so it is opened as root). Exits 1 with the failing step, and prints the app's crash log when it has one.
"""

import argparse
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

from mock_server import MockServer

PACKAGE = "app.baihe.shell"
PORT = 8765
ADDRESS = f"http://10.0.2.2:{PORT}"


def adb(*args, check=True, timeout=60):
    done = subprocess.run(["adb", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout)
    if check and done.returncode != 0:
        raise SystemExit(f"adb {' '.join(args)} failed: {done.stderr.strip() or done.stdout.strip()}")
    return done.stdout


def shell(*args, **kw):
    return adb("shell", *args, **kw)


def _dump():
    shell("uiautomator", "dump", "/sdcard/smoke-ui.xml", check=False)
    xml = adb("exec-out", "cat", "/sdcard/smoke-ui.xml", check=False)
    try:
        return list(ET.fromstring(xml).iter("node"))
    except ET.ParseError:
        return []


def screen_nodes():
    nodes = _dump()
    # A freshly booted emulator on a small runner often makes its own launcher or Google app stall, and Android's
    # "isn't responding" box then covers the screen. Waiting it out is not the app's fault; the app's own box is
    # left alone so the test still fails on it.
    box = next((n for n in nodes if "isn't responding" in (n.get("text") or "")
                and "Baihe" not in (n.get("text") or "")), None)
    wait = next((n for n in nodes if n.get("text") == "Wait"), None)
    if box is not None and wait is not None:
        tap(wait)
        time.sleep(2)
        return _dump()
    return nodes


def find(text=None, desc=None):
    for node in screen_nodes():
        if text is not None and node.get("text") == text:
            return node
        if desc is not None and node.get("content-desc") == desc:
            return node
    return None


def wait_for(what, check, seconds=30):
    end = time.time() + seconds
    while time.time() < end:
        found = check()
        # An XML node with no children is falsy, so a leaf found on screen must be told apart from None by identity.
        if found is not None and found is not False and found != "":
            return found
        time.sleep(1)
    shown = " | ".join(filter(None, ((n.get("text") or n.get("content-desc")) for n in screen_nodes())))
    raise StepFailed(f"Timed out after {seconds}s waiting for {what}. On screen: {shown[:600]}")


def tap(node):
    x1, y1, x2, y2 = map(int, re.findall(r"\d+", node.get("bounds")))
    shell("input", "tap", str((x1 + x2) // 2), str((y1 + y2) // 2))


class StepFailed(Exception):
    pass


def resumed_activity():
    out = shell("dumpsys", "activity", "activities", check=False)
    for line in out.splitlines():
        if "ResumedActivity" in line and PACKAGE in line:
            return line.strip()
    return ""


def app_is_alive():
    return bool(shell("pidof", PACKAGE, check=False).strip())


def crash_report():
    """The app's own crash lines from the crash buffer, or '' when it has not crashed."""
    out = adb("logcat", "-b", "crash", "-d", check=False)
    return out if PACKAGE in out else ""


def check_no_crash(step):
    crash = crash_report()
    if crash:
        raise StepFailed(f"{step}: the app crashed.\n{crash[-3000:]}")
    if any((n.get("text") or "").startswith("Baihe hit a problem") for n in screen_nodes()):
        raise StepFailed(f"{step}: the app showed its own crash screen.")
    if not app_is_alive():
        raise StepFailed(f"{step}: the app process is gone.")


def run(apk):
    server = MockServer(PORT)
    server.start()
    steps = []

    def step(name, body):
        print(f"-> {name}", flush=True)
        body()
        check_no_crash(name)
        steps.append(name)

    try:
        adb("wait-for-device")
        adb("root", check=False)
        adb("wait-for-device")
        # Lets the launcher and Google services finish starting before the app is put in front of them.
        shell("input", "keyevent", "KEYCODE_HOME", check=False)
        time.sleep(20)
        adb("logcat", "-b", "all", "-c", check=False)
        adb("install", "-r", apk, timeout=180)
        # Granted up front so Android's notification prompt does not sit over the screens being checked.
        shell("pm", "grant", PACKAGE, "android.permission.POST_NOTIFICATIONS", check=False)
        shell("pm", "clear", PACKAGE, check=False)
        shell("pm", "grant", PACKAGE, "android.permission.POST_NOTIFICATIONS", check=False)

        def launch():
            shell("am", "start", "-W", "-n", f"{PACKAGE}/.MainActivity")
            wait_for("the Baihe address question", lambda: find(text="Baihe address"), 60)

        def sign_address():
            field = wait_for("the address box", lambda: next(
                (n for n in screen_nodes() if n.get("class") == "android.widget.EditText"), None))
            tap(field)
            shell("input", "text", ADDRESS)
            tap(wait_for("the OK button", lambda: find(text="OK")))
            wait_for("Studio to ask the server for its page", lambda: server.seen("/", "BaiheAndroid/", exact=True) and
                     server.seen("/__seen"), 45)

        def to_grab():
            tap(wait_for("the Grab tab", lambda: find(desc="Open Grab")))
            wait_for("the Grab screen", lambda: "GrabActivity" in resumed_activity())

        def grab_page():
            shell("am", "start", "-n", f"{PACKAGE}/.GrabActivity",
                  "--es", "grab_command", "open", "--es", "grab_url", f"{ADDRESS}/chapter")
            wait_for("Grab to load the test page", lambda: server.seen("/chapter"), 30)

        def back_to_studio():
            tap(wait_for("the Studio tab", lambda: find(desc="Open Studio")))
            wait_for("the Studio screen", lambda: "MainActivity" in resumed_activity())

        def quick_read():
            shell("am", "start", "-n", f"{PACKAGE}/.QuickReadActivity")
            wait_for("the Quick read screen", lambda: "QuickReadActivity" in resumed_activity())

        def live():
            shell("am", "start", "-n", f"{PACKAGE}/.MainActivity", "--es", "route", "'#/live'")  # quoted: the phone's shell reads a bare # as a comment
            wait_for("Studio to open the Live route", lambda: server.seen("hash=%23%2Flive"), 30)

        step("Launch asks for the address", launch)
        step("Studio loads the server's page", sign_address)
        step("Grab tab opens", to_grab)
        step("Grab opens a test page", grab_page)
        step("Back to Studio", back_to_studio)
        step("Quick read screen opens", quick_read)
        step("Live route opens in Studio", live)
        # The last page's own start-up work (an update check, say) can crash a second after the page shows.
        time.sleep(5)
        check_no_crash("Settling after the last step")
    except StepFailed as e:
        print(f"FAILED: {e}", file=sys.stderr)
        print(adb("logcat", "-d", "-t", "300", check=False)[-6000:])
        return 1
    finally:
        server.stop()
    print(f"Phone smoke test passed: {len(steps)} steps.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apk", required=True)
    sys.exit(run(parser.parse_args().apk))
