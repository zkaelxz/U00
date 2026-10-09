"""
services/disk_usage_links.py -- how much a link, junction or other-volume
folder points at, for the Disk usage scan only. Read-only: nothing here is
used by Clear, Move, Restore or Trash, and a measured size never reaches a
path that changes files.

The scan counts a link as the link itself (its own few bytes) so a folder's
size is what lives inside it. What a link points at is reported apart, as
`linked_bytes`, so the same folder shows the same numbers whether it is looked
at from its parent or opened. Nothing here names a target path.
"""

import os
import re
import stat
import time

# Linked folders measured in one scan, and link paths remembered per folder.
# A folder with more links than this reports its linked size as incomplete.
MAX_LINK_TARGETS = 64

# Seconds the scan may spend on linked folders in all. The walk's own budget
# only checks the clock every few entries, which a slow network folder can
# stall inside one call; this cap also stops starting further targets.
MAX_LINK_SECONDS = 20

# A chain of links longer than this is treated as a loop.
MAX_LINK_HOPS = 8


def _is_local_target(raw: str) -> bool:
    """True only for the forms known to stay on this PC: a relative path, a
    drive-letter path (`X:\\...`) and the `\\\\?\\X:` / `\\\\.\\X:` forms of
    one. Everything else that starts with two separators (UNC, `\\\\?\\UNC`,
    `\\\\?\\GLOBALROOT\\...\\Mup`, named pipes, `\\\\?\\Volume{...}`) is refused:
    opening one makes Windows contact a host (possible NTLM credential
    exposure), connect to a pipe, or stall the scan while it holds the scan
    lock. This is an allowlist because the `\\\\?\\` and `\\\\.\\` prefixes
    reach devices and remote providers, not only drives."""
    p = raw.replace("/", "\\")
    if not p.startswith("\\\\"):
        return True
    return re.match(r"\\\\[?.]\\[A-Za-z]:(\\|$)", p) is not None


def _ancestors_local(path: str, depth: int) -> bool:
    """False when a folder on the way to `path` is a link that leads off this
    PC. Checked top-down so no lstat runs through an unchecked link: a target
    like `C:\\a\\b` is opened through `C:\\a`, which may itself be a symlink
    to a share."""
    chain = []
    parent = os.path.dirname(path)
    while parent and parent != os.path.dirname(parent):
        chain.append(parent)
        parent = os.path.dirname(parent)
    return all(
        not os.path.islink(p) or _local_target(p, depth + 1) is not None
        for p in reversed(chain)
    )


def _local_target(path: str, depth: int = 0):
    """The real path `path` leads to, or None when the chain of links starting
    at it (or at a folder on the way) reaches a non-local path, loops or is
    too long. Each hop's raw target is checked before it is followed, because
    realpath on Windows would open the whole chain, network host included, in
    one call."""
    if depth > MAX_LINK_HOPS:
        return None
    cur = path
    seen = set()
    for _ in range(MAX_LINK_HOPS + 1):
        if not _ancestors_local(cur, depth):
            return None
        try:
            raw = os.readlink(cur)
        except OSError:
            real = os.path.realpath(cur)
            return real if _is_local_target(real) else None
        if not _is_local_target(raw):
            return None
        key = os.path.normcase(cur)
        if key in seen:
            return None
        seen.add(key)
        cur = os.path.join(os.path.dirname(cur), raw)
    return None


class LinkedSizes:
    """The size of the folders links lead to, for one scan.

    `measure(path, parts, budget)` is the scan's own bounded walk, so a target
    spends the same entry and time budget as everything else and links inside
    a target are counted as themselves, never followed (no chains or loops).
    `within(path, folder)` is the data folder's path test."""

    def __init__(self, root: str, avoid: list, within, measure):
        self._root = root
        self._avoid = avoid
        self._within = within
        self._measure = measure
        self._done = []
        self._deadline = time.monotonic() + MAX_LINK_SECONDS

    def of(self, link_paths: list, budget, cut: bool = False):
        """(bytes, files, complete) for the folders `link_paths` lead to, or
        None when none of them was measured here (a file link, a target that
        is counted where it lives, or one that is skipped). A target that is
        not local, or a chain of links that loops, is too long or reaches one,
        is never touched and makes the result incomplete; so does a target
        that overlaps one already measured in this scan."""
        total = files = 0
        measured = skipped = False
        complete = not cut
        for path in link_paths:
            if time.monotonic() > self._deadline or (budget is not None and budget.hit):
                skipped = True
                break
            real = _local_target(path)
            if real is None:
                skipped = True
                continue
            try:
                is_dir = stat.S_ISDIR(os.stat(real).st_mode)
            except OSError:
                continue
            if not is_dir or self._skip(path, real):
                continue
            if self._overlaps_measured(real):
                # The same bytes are already in another item's linked size, so
                # this one shows less than it would opened alone.
                skipped = True
                continue
            if len(self._done) >= MAX_LINK_TARGETS:
                complete = False
                break
            self._done.append(real)
            found = self._measure(real, (), budget)
            total += found.size
            files += found.files
            measured = True
            complete = complete and not found.unreadable and not budget.hit
        if skipped:
            # Reported as incomplete rather than silently counted as empty.
            return total, files, False
        return (total, files, complete) if measured else None

    def _skip(self, path: str, real: str) -> bool:
        # A target inside the data folder is counted where it lives, unless
        # `path` is itself that folder (a mounted volume), which the walk
        # did not enter.
        if self._within(real, self._root) and os.path.normcase(real) != os.path.normcase(path):
            return True
        # A target that holds the data folder, the program or the user's
        # folders is a whole drive or profile, not a place Baihe stores data.
        if any(self._within(a, real) for a in [self._root, *self._avoid]):
            return True
        return False

    def _overlaps_measured(self, real: str) -> bool:
        # Part of / holding a measured target: counting it again would count
        # the same bytes twice.
        return any(self._within(real, d) or self._within(d, real) for d in self._done)
