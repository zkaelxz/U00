/** Keeps a Watch bookmark list in time order. */
import type { WatchBookmark } from '../../types/reader'

/** The list with `saved` placed in time order, ties by id. */
export function withBookmark(all: WatchBookmark[], saved: WatchBookmark): WatchBookmark[] {
  return [...all, saved].sort((a, b) => a.at_seconds - b.at_seconds || a.id - b.id)
}
