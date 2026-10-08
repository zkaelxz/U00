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
import stat

# Linked folders measured in one scan, and link paths remembered per folder.
# A folder with more links than this reports its linked size as incomplete.
MAX_LINK_TARGETS = 64


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

    def of(self, link_paths: list, budget, cut: bool = False):
        """(bytes, files, complete) for the folders `link_paths` lead to, or
        None when none of them was measured here (a file link, a target that
        is counted where it lives, or one that is skipped)."""
        total = files = 0
        measured = False
        complete = not cut
        for path in link_paths:
            real = os.path.realpath(path)
            try:
                is_dir = stat.S_ISDIR(os.stat(real).st_mode)
            except OSError:
                continue
            if not is_dir or self._skip(path, real):
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
        # Already measured, or part of / holding a measured target: counting
        # it again would count the same bytes twice.
        return any(self._within(real, d) or self._within(d, real) for d in self._done)
