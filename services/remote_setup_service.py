"""services/remote_setup_service.py -- where the owner is in the remote-access setup.

UI-free. Reads the loaded settings, the user list and the last health check
and answers one state per setup step, with a fixed message. It looks outward
only in three ways, all bounded: one loopback connect to the household
listener, one DNS lookup of the public name (and the public-address check,
if the owner set one), and, once remote access is on, a read-only
`netsh ... show rule` for the firewall rule. It never returns the public
name, the Google id or secret, an address or a path: states and fixed
sentences only.

The router forward can't be seen from the PC, so it stays `manual` and the
page says "yours to do" instead of guessing. `docs/household-access.md` holds the full steps.
"""
import ipaddress
import os
import re
import sys
from urllib.parse import urlsplit

import db
from lib import proc as proc_run
from lib.errors import InvalidInputError
from services import remote_health_service, settings_service

# Tells the page who acts: the app can do it, or the owner has to.
WHO_APP = "app"
WHO_YOU = "you"
WHO_MENU = "menu"   # the Start-menu item "Baihe Studio service", which asks for administrator rights

LISTENER_HOST = "127.0.0.1"

# The rule the Start-menu helper tells the owner to add (installer/service_control.py);
# only shown here, never added or changed by the app.
FIREWALL_RULE_NAME = "Baihe Studio remote access - Caddy HTTPS"
NETSH_TIMEOUT = 8.0

PUBLIC_URL_ENV = "BAIHE_PUBLIC_URL"
CLIENT_ID_ENV = "BAIHE_GOOGLE_CLIENT_ID"
CLIENT_SECRET_ENV = "BAIHE_GOOGLE_CLIENT_SECRET"
MAX_VALUE = 256
# Google gives every Web application client an id ending like this; a
# different shape is nearly always the secret pasted in the wrong box.
_CLIENT_ID_SUFFIX = ".apps.googleusercontent.com"
_BAD_NAME = ("The name must be a DNS name like baihe.your-domain: https only, no port, path, "
             "IP address or localhost. A non-ASCII name goes in its xn-- form.")
_SAFE_VALUE = re.compile(r"[A-Za-z0-9._~+/=-]+")


def _step(step_id, title, state, message, who):
    return {"id": step_id, "title": title, "state": state, "message": message, "who": who}


def _file_value(file_env, name):
    return (file_env.get(name) or "").strip()


def _fields_and_restart(settings, file_env) -> tuple:
    """Which settings exist (in .env or loaded) and whether .env holds one that
    isn't loaded: Baihe reads them at start-up. Booleans only."""
    loaded = {PUBLIC_URL_ENV: settings.public_url, CLIENT_ID_ENV: settings.google_client_id,
              CLIENT_SECRET_ENV: settings.google_client_secret}
    restart = False
    for name, now in loaded.items():
        saved = _file_value(file_env, name)
        if name == PUBLIC_URL_ENV:
            saved = saved.rstrip("/")
        if saved and saved != now:
            restart = True
    fields = {"public_url": bool(loaded[PUBLIC_URL_ENV] or _file_value(file_env, PUBLIC_URL_ENV)),
              "google_client_id": bool(loaded[CLIENT_ID_ENV] or _file_value(file_env, CLIENT_ID_ENV)),
              "google_client_secret": bool(loaded[CLIENT_SECRET_ENV]
                                           or _file_value(file_env, CLIENT_SECRET_ENV))}
    return fields, restart


def _dns_note(settings) -> tuple:
    """(state, sentence) for the public name's DNS record: one lookup, nothing
    but a fixed sentence returned. `unknown` when it can't be told."""
    target = remote_health_service.public_target(settings.public_url)
    if target is None:
        return "unknown", ""
    try:
        answers = remote_health_service._bounded(
            lambda: remote_health_service._resolve(target[0]), remote_health_service.RESOLVE_TIMEOUT)
    except Exception:
        return "problem", ("The name doesn't resolve yet. Create its DNS record (A, and AAAA "
                           "if your ISP gives a public IPv6) and wait a few minutes.")
    if answers and not any(a.is_global for a in answers):
        return "problem", ("The name resolves to a private address here. Use the public DNS "
                           "record only, with no local DNS entry for it.")
    ddns = remote_health_service.check_ddns(target[0])
    if ddns["state"] in ("warn", "critical"):
        return "problem", ddns["message"]
    if ddns["state"] == "ok":
        return "done", "The name resolves, and it points to this PC's public address."
    return "done", ("The name resolves. Set a public address check (below) to also confirm it "
                    "points to this PC.")


def _name_step(settings):
    title = "Choose the public name"
    if settings.public_url_error:
        return _step("name", title, "problem",
                     f"{settings.public_url_error} Enter a valid name below, then restart Baihe.", WHO_YOU)
    if settings.public_url:
        state, note = _dns_note(settings)
        if state == "problem":
            return _step("name", title, "problem", note, WHO_YOU)
        return _step("name", title, "done",
                     " ".join(t for t in ("A public name is set.", note,
                                          "Keep its record pointing at your home (dynamic DNS).") if t),
                     WHO_YOU)
    return _step("name", title, "todo",
                 "Pick a name such as baihe.your-domain, point its DNS record at your "
                 "home, then enter it below.", WHO_YOU)


def _google_step(settings):
    title = "Create the Google sign-in client"
    has_id, has_secret = bool(settings.google_client_id), bool(settings.google_client_secret)
    if has_id and has_secret:
        return _step("google", title, "done", "The client id and secret are set.", WHO_YOU)
    if has_id or has_secret:
        return _step("google", title, "problem",
                     "Only one of the client id and secret is set. Both are needed.", WHO_YOU)
    return _step("google", title, "todo",
                 "In Google Cloud Console, create a Web application OAuth client whose "
                 "redirect address is https://your-name/api/auth/callback, then enter its id "
                 "and secret below. Never paste the secret in a chat.",
                 WHO_YOU)


def _people_step(users):
    title = "Allow the people who may sign in"
    active = [u for u in users if u.get("is_active")]
    admins = [u for u in active if u.get("is_admin")]
    members = len(active) - len(admins)
    if not admins:
        return _step("people", title, "todo",
                     "No admin yet. Run `python -m api grant-admin <your email>` on this PC.",
                     WHO_YOU)
    if not members:
        return _step("people", title, "done",
                     "You are the only person allowed so far. Add the others in People "
                     "and access > People > Add a person.", WHO_APP)
    return _step("people", title, "done",
                 f"{members} member{'s' if members != 1 else ''} besides the admin can sign in.",
                 WHO_APP)


def _turn_on_step(settings, listener):
    title = "Turn remote access on"
    on = remote_health_service.remote_access_enabled(settings.public_url, settings.household_port)
    if not on:
        return _step("turn_on", title, "todo",
                     "Start menu > Baihe Studio service > 3, Turn remote access on. It asks "
                     "for administrator rights and checks the settings above first.", WHO_MENU)
    if listener["state"] != "ok":
        return _step("turn_on", title, "problem", listener["message"], WHO_MENU)
    return _step("turn_on", title, "done", "Remote access is on and the household listener "
                 "answers.", WHO_MENU)


def _netsh_path():
    return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "netsh.exe")


def _firewall_rule_visible():
    """True or False when `netsh ... show rule` answered, None when it can't be
    asked (not Windows, or it failed to run). Read-only: never adds a rule."""
    if sys.platform != "win32":
        return None
    try:
        run = proc_run.run_captured(
            [_netsh_path(), "advfirewall", "firewall", "show", "rule", f"name={FIREWALL_RULE_NAME}"],
            NETSH_TIMEOUT)
    except Exception:
        return None
    if run.timed_out or run.returncode is None:
        return None
    return run.returncode == 0


def _firewall_step(settings):
    title = "Allow Caddy through the Windows Firewall"
    how = ("Turning remote access on prints one netsh command; the service menu's status (1) "
           "prints it again. Run it in an administrator prompt.")
    if not remote_health_service.remote_access_enabled(settings.public_url, settings.household_port):
        return _step("firewall", title, "manual", how, WHO_YOU)
    seen = _firewall_rule_visible()
    if seen is None:
        return _step("firewall", title, "manual",
                     f"The app couldn't ask Windows about the rule. {how}", WHO_YOU)
    if seen:
        return _step("firewall", title, "done",
                     "The rule exists. It covers Private and Domain networks only: if Windows "
                     "calls your home network Public, change it to Private.", WHO_YOU)
    return _step("firewall", title, "todo",
                 f"No such rule was found (or this account can't see it). {how}", WHO_YOU)


def _manual_steps():
    return [
        _step("router", "Forward port 443 on your router", "manual",
              "Forward TCP 443 to this PC and reserve its address in the router. Nothing on "
              "this PC can see the router, so check it from a phone on mobile data.", WHO_YOU),
    ]


def _outside_step(settings):
    title = "Check it from outside"
    if not remote_health_service.remote_access_enabled(settings.public_url, settings.household_port):
        return _step("outside", title, "manual", "Open the public name on a phone on mobile "
                     "data once remote access is on.", WHO_YOU)
    health = remote_health_service.get_status(settings.public_url, settings.household_port)
    if health["state"] == "ok":
        return _step("outside", title, "done", health["message"], WHO_APP)
    if health["state"] in ("warn", "critical"):
        return _step("outside", title, "problem", health["message"], WHO_APP)
    return _step("outside", title, "manual", "Open the public name on a phone on mobile data "
                 "and sign in with an allowed account.", WHO_YOU)


def get_setup_status(settings, env_path=None) -> dict:
    """{steps, next, fields, restart_needed}. `state` is done, todo, problem
    or manual (not visible from here); `next` is the first step not done."""
    listener = remote_health_service.check_listener(LISTENER_HOST, settings.household_port)
    steps = [_name_step(settings), _google_step(settings), _people_step(db.auth_list_users()),
             _turn_on_step(settings, listener), _firewall_step(settings), *_manual_steps(),
             _outside_step(settings)]
    pending = next((s["id"] for s in steps if s["state"] != "done"), None)
    fields, restart = _fields_and_restart(settings, settings_service.read_env_file(env_path))
    return {"steps": steps, "next": pending, "fields": fields, "restart_needed": restart}


# --- saving the sign-in settings (PC only) ---------------------------------

def _clean(value, what):
    """Fixed messages only: the value may be the Google secret."""
    value = (value or "").strip()
    if not value or len(value) > MAX_VALUE or not _SAFE_VALUE.fullmatch(value):
        raise InvalidInputError(f"The {what} is empty, too long or has characters that "
                                "aren't allowed. Paste it again without spaces.")
    return value


def _clean_public_url(value) -> str:
    """Just https://name, as Baihe's start-up check wants it (api.api_config
    holds that rule but services may not import the api layer)."""
    text = (value or "").strip()
    if text and "://" not in text:
        text = f"https://{text}"
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        raise InvalidInputError(_BAD_NAME) from None
    host = parts.hostname or ""
    try:
        ipaddress.ip_address(host)
        is_ip = True
    except ValueError:
        is_ip = False
    if (parts.scheme != "https" or "@" in parts.netloc or port is not None
            or parts.path.strip("/") or parts.query or parts.fragment or is_ip
            or "." not in host or not host.isascii()):
        raise InvalidInputError(_BAD_NAME)
    return f"https://{host.lower()}"


def save_sign_in(settings, public_url, client_id, client_secret, env_path=None) -> dict:
    """Validates every given value first, then writes them to .env; a field
    that is None keeps what .env holds. Returns the new setup status, where
    `restart_needed` says Baihe hasn't loaded them yet. Nothing here logs or
    returns a value."""
    updates = {}
    if public_url is not None:
        updates[PUBLIC_URL_ENV] = _clean_public_url(public_url)
    if client_id is not None:
        value = _clean(client_id, "client id")
        if not value.endswith(_CLIENT_ID_SUFFIX):
            raise InvalidInputError("A Google client id ends with .apps.googleusercontent.com. "
                                    "The secret goes in the other box.")
        updates[CLIENT_ID_ENV] = value
    if client_secret is not None:
        value = _clean(client_secret, "client secret")
        if len(value) < 10 or value.endswith(_CLIENT_ID_SUFFIX):
            raise InvalidInputError("That doesn't look like a client secret. The id goes in "
                                    "the other box.")
        updates[CLIENT_SECRET_ENV] = value
    if not updates:
        raise InvalidInputError("Nothing to save.")
    for name, value in updates.items():
        settings_service.write_env_var(name, value, env_path)
    return get_setup_status(settings, env_path)
