"""
lib/device_label.py -- the coarse device name stored for a sign-in.

Lives in lib/ so the database migration that labels old sessions can use it
without importing services.
"""

# (token, name), first match wins. Order matters: Edge, Opera and Samsung
# Internet also say "Chrome/", Chrome and Firefox on iOS say "Safari/", and
# an iPhone says "like Mac OS X".
_BROWSERS = (("Edg", "Edge"), ("EdgiOS", "Edge"), ("EdgA", "Edge"), ("OPR", "Opera"),
             ("SamsungBrowser", "Samsung Internet"), ("FxiOS", "Firefox"),
             ("Firefox", "Firefox"), ("CriOS", "Chrome"), ("Chrome", "Chrome"),
             ("Safari", "Safari"))
_SYSTEMS = (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"),
            ("CrOS", "ChromeOS"), ("Windows", "Windows"), ("Macintosh", "Mac"),
            ("Linux", "Linux"))


def device_label(user_agent) -> str:
    """"Chrome on Android", "Safari on iPhone", "Browser on Windows",
    "Firefox browser" or "Unknown device", built only from the fixed names
    above, so no part of the raw user agent is ever stored or shown."""
    ua = str(user_agent or "")[:512]
    browser = next((name for token, name in _BROWSERS if f"{token}/" in ua), None)
    system = next((name for token, name in _SYSTEMS if token in ua), None)
    if system is None:
        return f"{browser} browser" if browser else "Unknown device"
    return f"{browser or 'Browser'} on {system}"
