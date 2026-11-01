import type { TitleKind } from './titleKind'

export type TitleView = 'simple' | 'full'

export const TITLE_VIEW_PREF = 'title.view'

// Only audio and video titles have two views: a novel has its own page, and a comic or a subtitle-only title keeps the plain workspace.
export const hasSimpleView = (kind: TitleKind): boolean => kind === 'audio' || kind === 'video'

// Full is today's workspace, so a viewer who never touched the switch sees nothing change.
export const asTitleView = (v: unknown): TitleView => (v === 'simple' ? 'simple' : 'full')

export interface EpisodeInput {
  id: number
  title_en: string | null
  title_zh: string | null
  episode_number: number | null
  line_count: number
  translated_count: number
}

export interface EpisodeRow {
  id: number
  title: string
  state: string
}

export function episodeState(lineCount: number, translatedCount: number): string {
  if (lineCount <= 0) return 'Not transcribed'
  if (translatedCount >= lineCount) return 'Transcribed · Translated'
  if (translatedCount <= 0) return 'Transcribed · Not translated'
  return `Transcribed · ${translatedCount} of ${lineCount} translated`
}

// A title with no episode number sorts after the numbered ones; the id keeps ties in the order they were added.
export function episodeRows(titles: EpisodeInput[]): EpisodeRow[] {
  const key = (t: EpisodeInput) => t.episode_number ?? Number.MAX_SAFE_INTEGER
  return [...titles]
    .sort((a, b) => key(a) - key(b) || a.id - b.id)
    .map((t) => ({
      id: t.id,
      title: t.title_en || t.title_zh || `Title #${t.id}`,
      state: episodeState(t.line_count, t.translated_count),
    }))
}
