import type { TitleKind } from './titleKind'

export type TitleView = 'simple' | 'full'

export const TITLE_VIEW_PREF = 'title.view'

// A subtitle-only title keeps the plain workspace.
export const hasSimpleView = (kind: TitleKind): boolean => kind === 'audio' || kind === 'video' || kind === 'novel' || kind === 'comic'

// One saved choice per group of kinds: someone who likes Full for videos should not lose the novel page.
export const titleViewPref = (kind: TitleKind): string =>
  kind === 'novel' ? `${TITLE_VIEW_PREF}.novel` : kind === 'comic' ? `${TITLE_VIEW_PREF}.comic` : TITLE_VIEW_PREF

// What a viewer who never touched the switch sees: the novel page was always the novel's landing page, and the rest keep today's workspace.
export const defaultTitleView = (kind: TitleKind): TitleView => (kind === 'novel' ? 'simple' : 'full')

// Stages leave out their advanced controls in Simple only where the stage is the same for every title of the kind (audio and video).
export const trimsStages = (kind: TitleKind): boolean => kind === 'audio' || kind === 'video'

// The stages a Simple page's buttons open; they get a slim page so Simple does not end in the full workspace's tab strip.
// Review is only linked from the audio and video page.
export const opensSimpleStage = (kind: TitleKind, stage: string | null): boolean =>
  hasSimpleView(kind) && (stage === 'source' || stage === 'translate' || stage === 'export' || (stage === 'review' && trimsStages(kind)))

export const asTitleView = (v: unknown, fallback: TitleView = 'full'): TitleView => (v === 'simple' || v === 'full' ? v : fallback)

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
