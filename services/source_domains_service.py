"""
services/source_domains_service.py -- the domain lists of sources that move
between domains (sources/domains.py): read and edit a source's list, and
confirm or dismiss a host that discovery proposed. UI-free; every route is
PC-only (api/routers/source_domains_routes.py).

Only host names leave this module, never a scheme-full URL, a path or a
query. Host names are a deliberate exception to "no fetched URLs": the
owner has to see which host they are confirming, which is why every route
is local_only().

Importing this module also sets the sources/domains.py unreachable hook:
when every listed domain of a source failed and discovery found nothing,
one maintenance-assistant backlog item is filed for that source (only while
the assistant's Developer Mode is on), and none again while that item is
still in the backlog.
"""

import re

from services.service_errors import InvalidInputError, NotFoundError, ServiceError
from sources import domains, health, registry, store
from translate_engines import redact_for_storage

MAX_DOMAINS = 10
# A plain DNS name with a dot and an alphabetic TLD: no scheme, port, path,
# userinfo or IP literal.
_HOST = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
                   r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$")
BAD_HOST = "Each domain must be a host name like example.com (no https://, port or path)."
BACKLOG_MARKER = "[source-domains:{source}]"


def _domain_classes() -> dict:
    return {name: cls for name, cls in registry.adapter_classes().items()
            if getattr(cls, "base_urls", None)}


def _require(name: str):
    cls = _domain_classes().get(name)
    if cls is None:
        raise NotFoundError("No source with a domain list by that name.")
    return cls


def _clean_host(value) -> str:
    if not isinstance(value, str):
        raise InvalidInputError(BAD_HOST)
    host = value.strip().lower().rstrip(".")
    if not _HOST.match(host):
        raise InvalidInputError(BAD_HOST)
    return host


def _entry(name: str, cls) -> dict:
    listed = domains.configured_origins(cls)
    last = store.last_good_domain(name)
    return {
        "source": name,
        "display_name": cls.display_name or name,
        "domains": [domains.host_of(o) for o in listed],
        "default_domains": [domains.host_of(u) for u in cls.base_urls],
        "customized": store.domain_list(name) is not None,
        "last_good": domains.host_of(last) if last in listed else None,
        "pending_proposals": len(store.domain_proposals(name)),
    }


def list_domains() -> list:
    return [_entry(name, cls) for name, cls in _domain_classes().items()]


def set_domains(name: str, hosts) -> dict:
    """Replaces the source's list (tried in this order; https only)."""
    cls = _require(name)
    if not isinstance(hosts, list) or not hosts:
        raise InvalidInputError("Give at least one domain.")
    if len(hosts) > MAX_DOMAINS:
        raise InvalidInputError(f"At most {MAX_DOMAINS} domains.")
    clean = []
    for value in hosts:
        host = _clean_host(value)
        if host not in clean:
            clean.append(host)
    store.set_domain_list(name, [f"https://{h}" for h in clean])
    return _entry(name, cls)


def reset_domains(name: str) -> dict:
    """Back to the adapter's own list; forgets the last good domain."""
    cls = _require(name)
    store.set_domain_list(name, None)
    store.set_last_good_domain(name, None)
    return _entry(name, cls)


def list_proposals() -> list:
    classes = _domain_classes()
    return [{"source": p["source"], "display_name": classes[p["source"]].display_name or p["source"],
             "host": p["host"], "found_at": p["found_at"]}
            for p in store.domain_proposals() if p["source"] in classes]


def _require_pending(name: str, host: str) -> str:
    host = _clean_host(host)
    if host not in {p["host"] for p in store.domain_proposals(name)}:
        raise NotFoundError("No pending proposal for that source and host.")
    return host


def confirm_proposal(name: str, host) -> dict:
    """Puts the host first on the source's list and makes it the last good
    domain, so the next request goes there. The source's backoff is lifted
    (the owner's explicit "try again now")."""
    cls = _require(name)
    host = _require_pending(name, host)
    listed = [domains.host_of(o) for o in domains.configured_origins(cls)]
    new = ([host] + [h for h in listed if h != host])[:MAX_DOMAINS]
    store.set_domain_list(name, [f"https://{h}" for h in new])
    store.set_last_good_domain(name, f"https://{host}")
    store.delete_domain_proposal(name, host)
    health.reset(name)
    return _entry(name, cls)


def dismiss_proposal(name: str, host) -> dict:
    """The host is not proposed for this source again."""
    _require(name)
    host = _require_pending(name, host)
    store.dismiss_domain_proposal(name, host)
    return {"dismissed": True}


def file_unreachable_backlog_item(source: str, display_name: str, probe_lines: list):
    """The sources/domains.py unreachable hook. Returns the backlog item, or
    None when the assistant is off, an item for this source is still in the
    backlog, or the backlog can't take one."""
    from services import maintenance_assistant_service as assistant
    if not assistant.developer_mode_enabled():
        return None
    marker = BACKLOG_MARKER.format(source=source)
    if any(str(item["text"]).startswith(marker) for item in assistant.list_backlog()["items"]):
        return None
    text = (f"{marker} {display_name}: every domain on its list is unreachable and no new "
            "address was found.\nProbe results:\n" + "\n".join(probe_lines or ["(none)"])
            + "\nUpdate the source's domain list under Sources on the PC, or the adapter.")
    text = redact_for_storage(text)[:assistant.MAX_BACKLOG_TEXT]
    try:
        return assistant.add_backlog_item("bug", text)
    except ServiceError:
        return None


domains.set_unreachable_hook(file_unreachable_backlog_item)
