"""services/remote_setup_service.py -- where the owner is in the remote-access setup.

UI-free. Reads the loaded settings, the user list and the last health check
and answers one state per setup step, with a fixed message. It opens no
connection except one loopback connect to the household listener, and it
never returns the public name, the Google id or secret, an address or a
path: states and fixed sentences only.

Some steps (the router forward, the firewall rule) can't be seen from the
app; they are `manual` so the page can say "yours to do" instead of
guessing. `docs/household-access.md` holds the full steps.
"""
import db
from services import remote_health_service

# Tells the page who acts: the app can do it, or the owner has to.
WHO_APP = "app"
WHO_YOU = "you"
WHO_MENU = "menu"   # the Start-menu item "Baihe Studio service", which asks for administrator rights

LISTENER_HOST = "127.0.0.1"


def _step(step_id, title, state, message, who):
    return {"id": step_id, "title": title, "state": state, "message": message, "who": who}


def _name_step(settings):
    title = "Choose the public name"
    if settings.public_url_error:
        return _step("name", title, "problem",
                     f"{settings.public_url_error} Fix it in the data folder's .env, "
                     "then restart Baihe.", WHO_YOU)
    if settings.public_url:
        return _step("name", title, "done",
                     "A public name is set. Keep its DNS record pointing at your home "
                     "(dynamic DNS).", WHO_YOU)
    return _step("name", title, "todo",
                 "Pick a name such as baihe.your-domain, point its DNS record at your "
                 "home, then set BAIHE_PUBLIC_URL in the data folder's .env.", WHO_YOU)


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
                 "redirect address is https://your-name/api/auth/callback, then put its id "
                 "and secret in the data folder's .env. Never paste the secret in a chat.",
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


def _manual_steps():
    return [
        _step("firewall", "Allow Caddy through the Windows Firewall", "manual",
              "Turning remote access on prints one netsh command. Run it in an "
              "administrator prompt. The app can't see the rule yet; the service menu's "
              "status (1) shows it.", WHO_YOU),
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


def get_setup_status(settings) -> dict:
    """{steps: [{id, title, state, message, who}], next}. `state` is done,
    todo, problem or manual (not visible from here); `next` is the first step
    not done."""
    listener = remote_health_service.check_listener(LISTENER_HOST, settings.household_port)
    steps = [_name_step(settings), _google_step(settings), _people_step(db.auth_list_users()),
             _turn_on_step(settings, listener), *_manual_steps(), _outside_step(settings)]
    pending = next((s["id"] for s in steps if s["state"] != "done"), None)
    return {"steps": steps, "next": pending}
