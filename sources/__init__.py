"""
sources -- the raw-source adapter system.

A separate domain from the drama/line core: it finds and fetches raw
content (comic pages, novel chapters, video URLs) from outside sources
and hands it to the existing Scanlate / Workspace paths unchanged.

  models.py          vocabulary: tiers, failure reasons, statuses, records
  base.py            the SourceAdapter interface
  http.py            the one paced client every request goes through
  detect.py          naming what a response shows (challenge, geo, SPA...)
  ladder.py          the access-method ladder + capabilities / Test Now
  health.py          🟢/🟡/🔴 per source, with backoff
  cache.py           the configurable raw-content cache
  store.py           persistence (its own sources.db)
  registry.py        adapter registration + multi-source search
  chapter_order.py   natural/CJK chapter sorting
  chapter_check.py   scheduled new-chapter checks (notify, don't download)
  generic_import.py  one-off paste-a-URL import (comic pages / novel text)
  front_door.py      "paste any URL": classify, preview, route
  pipeline.py        hand-off into Scanlate / Workspace + import jobs
  mock.py            the offline demo source
  adapters/          real site adapters

See docs/adding-source.md for how to add an adapter.
"""
