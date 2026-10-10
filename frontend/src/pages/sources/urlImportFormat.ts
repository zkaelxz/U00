// Pure logic and copy for the S-4/S-5 import UI: the paste-a-URL box, the
// series chapter import and the Workspace "From a URL" download. No React
// here, so everything is unit-tested in urlImportFormat.test.ts.
import type { DramaSummary } from '../../api/types'
import { safeDetail } from '../../components/errorMessages'
import type { DramaCreateRequest } from '../../types/library'
import type { SeriesChapter } from '../../types/sources'
import type {
  ChapterImportResult, ChapterImportRow, ChapterSaveResult, ChapterSaveRow, ImportState, UrlImportResult, UrlPreview,
} from '../../types/sourcesImport'

// While false, other devices cannot import or track from the UI (PC only),
// same as searching (SEARCH_REMOTE_ALLOWED); see docs/remote-access-decision.md.
export const IMPORT_REMOTE_ALLOWED = false

export const MAX_URL_LEN = 2000
// The chapter import route accepts 1..200 chapter ids per request.
export const MAX_CHAPTERS = 200

// ---------------------------------------------------------------- links

/** Why a pasted link can't be sent, or null when it looks fine. */
export function checkUrl(text: string): string | null {
  const t = text.trim()
  if (!t) return 'Still needed: a link.'
  if (t.length > MAX_URL_LEN) return `That link is too long (max ${MAX_URL_LEN} characters).`
  if (/\s/.test(t) || !/^https?:\/\//i.test(t)) return 'Enter a link that starts with http:// or https://.'
  let u: URL
  try {
    u = new URL(t)
  } catch {
    return 'That link is not valid.'
  }
  if (!u.hostname) return 'That link is not valid.'
  if (u.username || u.password) return 'Links with a user name or password are not supported.'
  return null
}

// ---------------------------------------------------------------- preview

const TYPE_LABELS: Record<string, string> = { video: 'Video', novel: 'Novel', comic: 'Comic', unknown: 'Unknown' }

export const contentTypeLabel = (t: string | null | undefined) => TYPE_LABELS[t ?? 'unknown'] ?? 'Unknown'

const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`

/** The small facts line under the preview title. */
export function previewFacts(p: UrlPreview): string[] {
  const out: string[] = []
  if (p.platform) out.push(p.platform)
  if (p.chapter) out.push(p.chapter)
  if (p.language) out.push(p.language)
  if (typeof p.chapter_count === 'number') out.push(plural(p.chapter_count, 'chapter'))
  if (typeof p.text_length === 'number' && p.text_length > 0) out.push(plural(p.text_length, 'character'))
  if (typeof p.image_count === 'number' && p.image_count > 0) out.push(plural(p.image_count, 'image'))
  return out
}

type PreviewAction = 'series' | 'novel' | 'video' | 'comic' | 'unknown'

/** Which actions the preview card offers. A series or chapter link opens the series browser. */
export function previewAction(p: UrlPreview): PreviewAction {
  if ((p.route === 'series' || p.route === 'chapter') && p.adapter && p.series_id) return 'series'
  if (p.content_type === 'video') return 'video'
  if (p.content_type === 'novel') return 'novel'
  if (p.content_type === 'comic') return 'comic'
  return 'unknown'
}

// Comic pages are downloaded from the site itself, so pasted page source can't bring them in.
export const PASTED_COMIC_NOTE =
  'Comic pages can’t be imported from pasted page source. Once the site lets Baihe in again, press Preview and import the pages.'

export const PREVIEW_NOTES: Record<'unknown', string> = {
  unknown: 'Baihe couldn’t tell what this page is. Try the series or chapter page itself, or a different link.',
}

// ---------------------------------------------------------------- dramas

export const dramaLabel = (d: Pick<DramaSummary, 'id' | 'title_en' | 'title_zh'>) =>
  d.title_en?.trim() || d.title_zh?.trim() || `Title ${d.id}`

const COMIC_MEDIA_TYPES = ['manhua', 'manga', 'manhwa']

/** Dramas a chapter import may write into: comic types for page sources, novels for text sources. */
export function chapterImportDramas(dramas: DramaSummary[], comic: boolean): DramaSummary[] {
  return dramas.filter((d) => (comic ? COMIC_MEDIA_TYPES.includes(d.media_type ?? '') : d.media_type === 'novel'))
}

/** How many titles chapterImportDramas leaves out. */
export const hiddenDramaCount = (dramas: DramaSummary[], comic: boolean) => dramas.length - chapterImportDramas(dramas, comic).length

/** Why the import list is short, so a missing title is not a mystery. */
export function hiddenDramasNote(count: number, comic: boolean): string {
  const others = count === 1 ? '1 other title isn’t' : `${plural(count, 'other title')} aren’t`
  return comic
    ? `Showing comic titles only. ${others} listed because manga pages can’t go into a novel or audio title. Choose “New title…” to make a comic title.`
    : `Showing novel titles only. ${others} listed because chapter text can only go into a novel title. Choose “New title…” to make a novel title.`
}

// Mirrors services/media_upload_service._UPLOAD_CONTENT_MODES (null = audio_drama).
const URL_MEDIA_CONTENT_MODES = ['audio_drama', 'streamer_vod']

export const canTakeMedia = (contentMode: string | null | undefined) =>
  URL_MEDIA_CONTENT_MODES.includes(contentMode || 'audio_drama')

export function videoDramas(dramas: DramaSummary[]): DramaSummary[] {
  return dramas.filter((d) => canTakeMedia(d.content_mode))
}

/** Audio only is on by default, except for a streamer VOD (the video is the point). */
export const defaultAudioOnly = (contentMode: string | null | undefined) => contentMode !== 'streamer_vod'

const COMIC_BY_LANGUAGE: Record<string, string> = { zh: 'manhua', ja: 'manga', ko: 'manhwa' }
const LANGUAGES = ['zh', 'ja', 'ko']

/** The POST /api/dramas body for "New drama…": title, language and a media type the import accepts. */
export function newDramaRequest(title: string, language: string | null | undefined, comic: boolean): DramaCreateRequest {
  const lang = LANGUAGES.includes((language ?? '').toLowerCase().slice(0, 2)) ? (language ?? '').toLowerCase().slice(0, 2) : 'zh'
  const t = title.trim()
  // Titles in CJK script go in the original-title field.
  const cjk = /[぀-ヿ㐀-鿿가-힯]/.test(t)
  return {
    source_language: lang,
    ...(cjk ? { title_zh: t } : { title_en: t }),
    media_type: comic ? COMIC_BY_LANGUAGE[lang] : 'novel',
  }
}

// ---------------------------------------------------------------- chapter selection

export function toggleId(selected: string[], id: string, on: boolean): string[] {
  if (on) return selected.includes(id) ? selected : [...selected, id]
  return selected.filter((x) => x !== id)
}

export const allSelected = (selected: string[], chapters: SeriesChapter[]) =>
  chapters.length > 0 && chapters.every((c) => selected.includes(c.chapter_id))

/** The chapter_ids to send: in the series' own order, only ids that are in the list. */
export function importIds(selected: string[], chapters: SeriesChapter[]): string[] {
  const set = new Set(selected)
  return chapters.filter((c) => set.has(c.chapter_id)).map((c) => c.chapter_id)
}

export const importLabel = (n: number) => `Import ${plural(n, 'chapter')}`

/** Why Import is disabled, or null when it can run. */
export function importReason(count: number, dramaId: number | null): string | null {
  if (count === 0) return 'Still needed: at least one chapter.'
  if (count > MAX_CHAPTERS) return `Import at most ${MAX_CHAPTERS} chapters at a time.`
  if (!dramaId) return 'Still needed: a title to import into.'
  return null
}

// ---------------------------------------------------------------- outcomes

export function outcomeSummary(r: ChapterImportResult): string {
  const parts = [`${r.imported_count} imported`]
  if (r.skipped_count) parts.push(`${r.skipped_count} already there`)
  if (r.failed_count) parts.push(`${r.failed_count} failed`)
  const missing = r.chapters.filter((c) => c.outcome === 'not_found').length
  if (missing) parts.push(`${missing} not found`)
  const notTried = r.not_attempted_count ?? r.chapters.filter((c) => c.outcome === 'not_attempted').length
  if (notTried) parts.push(`${notTried} not attempted`)
  const needsAi = r.chapters.filter((c) => c.outcome === 'needs_ai').length
  if (needsAi) parts.push(`${needsAi} need AI help`)
  return (r.cancelled ? 'Stopped. ' : '') + parts.join(' · ')
}

export function outcomeText(c: ChapterImportRow): string {
  if (c.outcome === 'imported') {
    if (typeof c.pages === 'number') return `Imported · ${plural(c.pages, 'page')}`
    if (typeof c.chars === 'number') return `Imported · ${plural(c.chars, 'character')}`
    return 'Imported'
  }
  if (c.outcome === 'skipped') return 'Already imported'
  if (c.outcome === 'not_found') return 'No longer on the site'
  if (c.outcome === 'not_attempted') return 'Not attempted'
  if (c.outcome === 'needs_ai') return 'Needs AI help'
  if (c.outcome === 'failed') {
    const why = c.error ? safeDetail(c.error) : null
    return why ? `Failed: ${why}` : 'Failed'
  }
  return String(c.outcome)
}

export const outcomeTone = (outcome: string) =>
  outcome === 'imported' ? 'ok'
    : outcome === 'failed' ? 'bad'
      : outcome === 'not_found' || outcome === 'not_attempted' || outcome === 'needs_ai' ? 'warn' : 'muted'

/** Comic imports: the note shown after pages are stored. */
export function comicNote(r: ChapterImportResult): string | null {
  const pages = r.chapters.reduce((n, c) => n + (c.outcome === 'imported' && typeof c.pages === 'number' ? c.pages : 0), 0)
  if (!pages) return null
  return `Imported ${plural(pages, 'page')}. There’s no page viewer here yet; they’ll show in Scanlate and Export later.`
}

export function urlImportText(r: UrlImportResult): string {
  if (r.needs_review && r.review_open && (r.pages_found ?? 0) > 1) {
    return `Read ${plural(r.pages_found ?? 0, 'page')}. Nothing was saved yet. Check the pages below, then import the ones you want.`
  }
  if (r.needs_review && r.review_open) return 'Nothing was saved yet. Check what Baihe found below, then import it.'
  if (r.needs_review) {
    return 'Baihe couldn’t be sure it found the chapter text, so nothing was saved. Paste the text in the Workspace’s Novel panel instead.'
  }
  return `Added ${plural(r.char_count, 'character')} to the title’s novel text.`
}

// ---------------------------------------------------------------- import state

export type ChapterMark = { label: string; tone: 'ok' | 'bad' | 'warn'; note: string | null }

/** Picker marks per chapter id: already imported, or left failed / not attempted by an earlier run. */
export function chapterMarks(state: ImportState | null): Map<string, ChapterMark> {
  const marks = new Map<string, ChapterMark>()
  if (!state) return marks
  for (const id of state.imported_chapter_ids) marks.set(id, { label: 'Imported', tone: 'ok', note: null })
  for (const r of state.retry) {
    if (marks.has(r.chapter_id)) continue
    if (r.status === 'partial') {
      marks.set(r.chapter_id, { label: 'Check first', tone: 'bad', note: r.error ? safeDetail(r.error) : null })
      continue
    }
    if (r.status === 'needs_ai') {
      marks.set(r.chapter_id, { label: 'Needs AI help', tone: 'warn', note: null })
      continue
    }
    const failed = r.status === 'failed'
    const why = failed && r.error ? safeDetail(r.error) : null
    marks.set(r.chapter_id, { label: failed ? 'Failed' : 'Not attempted', tone: failed ? 'bad' : 'warn', note: why })
  }
  return marks
}

/** The chapters waiting for the person to confirm an AI read. */
export const needsAiRows = (state: ImportState | null) => state?.retry.filter((r) => r.status === 'needs_ai') ?? []

/** What Select all ticks: every chapter not already imported into the drama. */
export function selectableChapters(chapters: SeriesChapter[], state: ImportState | null): SeriesChapter[] {
  if (!state?.imported_chapter_ids.length) return chapters
  const done = new Set(state.imported_chapter_ids)
  return chapters.filter((c) => !done.has(c.chapter_id))
}

export function selectAllLabel(selectable: number, total: number): string {
  return selectable === total ? `Select all ${total}` : `Select all ${selectable} not yet imported`
}

/** The retry set: the drama's saved import state, else the run that just finished.
 * A "partial" chapter (interrupted mid-write) and a "needs_ai" one (waiting for the
 * person's AI confirm) are never retried automatically. */
export function retryIds(state: ImportState | null, result: ChapterImportResult | null): string[] {
  if (state) return state.retry.filter((r) => r.status !== 'partial' && r.status !== 'needs_ai').map((r) => r.chapter_id)
  return result?.retry_chapter_ids ?? []
}

export const retryLabel = (n: number) => `Retry failed chapters (${n.toLocaleString('en-US')})`

/** Said next to Retry when the set is over the per-request cap. */
export const retryNote = (n: number) =>
  n > MAX_CHAPTERS ? `Retries the first ${MAX_CHAPTERS} of ${n.toLocaleString('en-US')}; run it again for the rest.` : null

// ---------------------------------------------------------------- URL download

/** Why Download is disabled, or null. */
export function downloadReason(url: string, hasAudio: boolean, confirmReplace: boolean): string | null {
  const bad = checkUrl(url)
  if (bad) return bad
  if (hasAudio && !confirmReplace) return 'Still needed: tick “Replace the current audio”.'
  return null
}

// ---------------------------------------------------------------- save as CBZ

export const saveLabel = (n: number) => `Save ${plural(n, 'chapter')} as CBZ`

/** Why Save as CBZ is disabled, or null when it can run. */
export function saveReason(count: number): string | null {
  if (count === 0) return 'Still needed: at least one chapter.'
  if (count > MAX_CHAPTERS) return `Save at most ${MAX_CHAPTERS} chapters at a time.`
  return null
}

export function saveSummary(r: ChapterSaveResult): string {
  const parts = [`${r.saved_count} saved`]
  if (r.skipped_count) parts.push(`${r.skipped_count} already saved`)
  if (r.failed_count) parts.push(`${r.failed_count} failed`)
  if (r.not_found_count) parts.push(`${r.not_found_count} not found`)
  if (r.not_attempted_count) parts.push(`${r.not_attempted_count} not attempted`)
  return (r.cancelled ? 'Stopped. ' : '') + parts.join(' · ')
}

export function saveOutcomeText(c: ChapterSaveRow): string {
  if (c.outcome === 'saved') return typeof c.pages === 'number' ? `Saved · ${plural(c.pages, 'page')}` : 'Saved'
  if (c.outcome === 'skipped') return 'Already saved'
  if (c.outcome === 'not_found') return 'No longer on the site'
  if (c.outcome === 'not_attempted') return 'Not attempted'
  const why = c.error ? safeDetail(c.error) : null
  return why ? `Failed: ${why}` : 'Failed'
}

export const saveOutcomeTone = (outcome: string) => (outcome === 'saved' ? 'ok' : outcomeTone(outcome))
