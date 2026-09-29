// Pure helpers for the Library list's "More filters" (inventory L07): the
// Streamlit tab's Studio, Author, Voice actor, Language, Type and Custom tags.
import type { DramaFilters } from '../api/types'

export interface MoreFilters {
  studio: string
  author: string
  voice_actor: string
  source_language: string
  media_type: string
  tags: string[]
}

export const NO_MORE_FILTERS: MoreFilters = {
  studio: '', author: '', voice_actor: '', source_language: '', media_type: '', tags: [],
}

/** How many of the "More filters" are set (each tag counts once). */
export function moreFilterCount(m: MoreFilters): number {
  const set = [m.studio, m.author, m.voice_actor, m.source_language, m.media_type].filter(Boolean).length
  return set + m.tags.length
}

/** The API query for the list: the always-visible filters plus the "More filters". */
export function listQuery(search: string, status: string, quickFilter: string, m: MoreFilters): DramaFilters {
  return {
    search, status, quick_filter: quickFilter,
    studio: m.studio, author: m.author, voice_actor: m.voice_actor,
    source_language: m.source_language, media_type: m.media_type, tag: m.tags,
  }
}

/** Adds or removes one tag, keeping the order the tags were picked in. */
export function toggleTag(tags: readonly string[], tag: string): string[] {
  return tags.includes(tag) ? tags.filter((t) => t !== tag) : [...tags, tag]
}

/**
 * The choices for a select: the known values, plus the current value if it is
 * no longer among them (so a stale pick stays visible and can be cleared).
 */
export function withCurrent(options: readonly string[], current: string): string[] {
  return current && !options.includes(current) ? [current, ...options] : [...options]
}
