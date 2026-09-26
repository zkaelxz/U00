"""
sources/registry.py -- which adapters exist, which are switched on, and
the top-level multi-source search (Step 23 item 6).
"""

import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import store
from .models import NotSupportedError, SourceError

_ADAPTERS = {}


def register(cls):
    """Class decorator: makes an adapter available to the app."""
    if not cls.name:
        raise ValueError("An adapter needs a `name`.")
    _ADAPTERS[cls.name] = cls
    return cls


def _load_builtin_adapters():
    # Imported for their @register side effect.
    from . import mock  # noqa: F401
    from . import adapters
    adapters.load_all()


def adapter_classes() -> dict:
    _load_builtin_adapters()
    return dict(_ADAPTERS)


def is_enabled(name: str) -> bool:
    cls = adapter_classes().get(name)
    if cls is None:
        return False
    if getattr(cls, "is_demo", False) and not store.get_setting("demo_source_enabled"):
        return False
    return name not in (store.get_setting("disabled_sources") or [])


def set_enabled(name: str, enabled: bool):
    disabled = set(store.get_setting("disabled_sources") or [])
    if enabled:
        disabled.discard(name)
    else:
        disabled.add(name)
    store.set_setting("disabled_sources", sorted(disabled))


def get_adapter(name: str, **client_kwargs):
    cls = adapter_classes().get(name)
    if cls is None:
        raise KeyError(f"No source adapter named {name!r}.")
    return cls(**client_kwargs)


def enabled_adapters(**client_kwargs) -> list:
    return [cls(**client_kwargs) for name, cls in adapter_classes().items() if is_enabled(name)]


def _matches(cls, url: str) -> bool:
    return any(re.search(p, url or "") for p in cls.url_patterns)


def find_for_url(url: str, **client_kwargs):
    for name, cls in adapter_classes().items():
        if is_enabled(name) and _matches(cls, url):
            return cls(**client_kwargs)
    return None


def adapter_class_for_url(url: str):
    """The adapter class whose URL patterns match, enabled or not -- a
    switched-off source's terms still apply to a link pasted for it."""
    return next((cls for cls in adapter_classes().values() if _matches(cls, url)), None)


# ---------------------------------------------------------------------------
# Multi-source search
# ---------------------------------------------------------------------------

_PUNCT = re.compile(r"[\W_]+", re.UNICODE)


def normalize_title(title: str) -> str:
    """Title key for de-duplication across sources that don't agree on
    formatting: width-normalized, case-folded, punctuation/space-free."""
    t = unicodedata.normalize("NFKC", title or "").casefold()
    return _PUNCT.sub("", t)


@dataclass
class MergedResult:
    title: str
    key: str
    entries: list = field(default_factory=list)     # [SearchResult], one per source hit

    @property
    def sources(self) -> list:
        return [e.source for e in self.entries]


@dataclass
class MultiSearchResult:
    results: list = field(default_factory=list)     # [MergedResult]
    errors: dict = field(default_factory=dict)      # source -> message
    per_source_counts: dict = field(default_factory=dict)


def multi_search(query: str, adapters=None, max_workers: int = 4) -> MultiSearchResult:
    """Fans `query` out to every adapter concurrently. Each adapter's own
    pacing still applies (it's per source, not per search); a slow or
    failing source only costs that source's results."""
    if adapters is None:
        adapters = enabled_adapters()
    adapters = [a for a in adapters if a.supports("search")]
    out = MultiSearchResult()
    if not adapters:
        return out

    def one(adapter):
        try:
            return adapter.name, list(adapter.search(query)), None
        except NotSupportedError as e:
            return adapter.name, [], str(e)
        except SourceError as e:
            return adapter.name, [], f"{e.reason.value}: {e}"
        except Exception as e:     # one bad adapter mustn't sink the others
            return adapter.name, [], f"{type(e).__name__}: {e}"

    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(adapters)))) as pool:
        outcomes = list(pool.map(one, adapters))

    merged = {}
    for name, results, err in outcomes:
        out.per_source_counts[name] = len(results)
        if err:
            out.errors[name] = err
        for r in results:
            key = normalize_title(r.title) or f"{r.source}:{r.series_id}"
            if key not in merged:
                merged[key] = MergedResult(title=r.title, key=key)
            merged[key].entries.append(r)
    out.results = list(merged.values())
    return out
