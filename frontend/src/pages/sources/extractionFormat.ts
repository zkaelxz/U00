// Pure logic and copy for the pasted-URL extraction extras (parity SO09):
// the AI fallback picker; SO06, the comic import's result; SO10, Review
// extraction. No React here; tested in extractionFormat.test.ts.
import { humanize } from '../../components/labels'
import type {
  AiEngines, AiRequestFields, ComicUrlImportResult, ExtractionFollow, ExtractionImage, ExtractionNovel, ExtractionReview,
  ImageChoice, NovelRerunRequest, ProfileSaved, ReviewImportResult,
} from '../../types/sourcesExtraction'

export interface AiChoice {
  on: boolean
  // null: the saved default engine.
  engine: string | null
}

export const AI_OFF: AiChoice = { on: false, engine: null }

export const AI_HELP =
  'Only used when Baihe can’t tell which part of the page is the chapter: one AI call for the page, ' +
  'and the site’s layout is remembered so its next chapter needs none. The AI only points at parts of ' +
  'the page; the text and images are copied from the page itself, never rewritten.'

/** The engine the request will use: the one picked, else the saved default. */
export function effectiveEngine(choice: AiChoice, engines: AiEngines | null): string | null {
  if (!choice.on) return null
  if (choice.engine && (!engines || engines.engines.includes(choice.engine))) return choice.engine
  return engines?.default ?? null
}

/** Why the fallback can't be used as set, or null. */
export function aiReason(choice: AiChoice, engines: AiEngines | null): string | null {
  if (!choice.on) return null
  if (!engines) return 'Loading the AI engines…'
  if (engines.engines.length === 0) return 'No AI engine can be used for this.'
  if (!effectiveEngine(choice, engines)) return 'Still needed: an AI engine.'
  return null
}

/** The fields to add to the import request (nothing when the fallback is off). */
export function aiRequestFields(choice: AiChoice, engines: AiEngines | null): AiRequestFields {
  const engine = effectiveEngine(choice, engines)
  return choice.on && engine ? { use_ai: true, engine } : {}
}

export const engineLabel = (name: string) => (name === 'ollama' ? 'Ollama (on this PC)' : humanize('engine', name))

const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`

// ---------------------------------------------------------------- following next-chapter links

export const MAX_FOLLOW_PAGES = 50
export const DEFAULT_FOLLOW_PAGES = 10

export const FOLLOW_HELP =
  'Also reads the chapters after this one by following each page’s “next chapter” link on the same site, up to ' +
  'this many pages in all. Nothing is saved until you have checked the list of pages.'

/** The follow_pages field to send: a whole number from 2 to 50, else nothing (one page, as before). */
export function followRequestFields(on: boolean, pages: number): Pick<AiRequestFields, 'follow_pages'> {
  if (!on || !Number.isFinite(pages)) return {}
  const n = Math.min(MAX_FOLLOW_PAGES, Math.trunc(pages))
  return n > 1 ? { follow_pages: n } : {}
}

/** A typed page count, kept within 1-50. */
export const clampFollowPages = (value: string) =>
  Math.max(1, Math.min(MAX_FOLLOW_PAGES, Math.trunc(Number(value) || 1)))

const FOLLOW_STOP: Record<string, string> = {
  cap: 'Stopped at the number of pages you asked for.',
  no_next: 'The last page has no next-chapter link.',
  cycle: 'The next-chapter link led back to a page already read.',
  other_host: 'The next-chapter link leads to another site, so it wasn’t followed.',
  downgrade: 'The next-chapter link drops from a secure (https) address to plain http, so it wasn’t followed.',
  gate: 'The next-chapter link leads to a sign-in, sign-out, age-check or payment page, so it wasn’t followed.',
  not_public: 'The next-chapter link isn’t a public web address, so it wasn’t followed.',
  handoff:
    'The site showed a verification page, so Baihe stopped there. The pages before it are listed; open the site in your ' +
    'browser to carry on from there.',
  unreachable: 'The next page couldn’t be loaded.',
  invalid: 'Baihe couldn’t find chapter text on the next page, so it stopped before it.',
  chars: 'Stopped at the size limit for one import.',
}

/** Why following stopped, in words. */
export function followStopText(f: ExtractionFollow): string {
  if (f.stop === 'invalid' && f.pages.length <= 1) {
    return 'Baihe isn’t sure about the text on this page, so it didn’t follow its next-chapter link.'
  }
  return FOLLOW_STOP[f.stop] ?? 'Stopped following next-chapter links.'
}

/** The ticked page ids, in reading order. */
export function pickedPages(f: ExtractionFollow, unticked: ReadonlySet<number>): number[] {
  return f.pages.map((p) => p.id).filter((id) => !unticked.has(id))
}

/** Characters in the ticked pages. */
export function pickedChars(f: ExtractionFollow, unticked: ReadonlySet<number>): number {
  return f.pages.filter((p) => !unticked.has(p.id)).reduce((n, p) => n + p.char_count, 0)
}

export const followPageLabel = (p: { id: number; title: string; char_count: number; host: string }) =>
  `${p.title || `Page ${p.id + 1}`} · ${plural(p.char_count, 'character')} · ${p.host}`

export function comicImportText(r: ComicUrlImportResult): string {
  if (r.needs_review) {
    return r.review_open
      ? 'Nothing was added yet. Check which images are the pages below, then import them.'
      : 'Baihe couldn’t be sure which images are the pages, so nothing was added.'
  }
  return `Added ${plural(r.pages_added, 'page')} to the drama.`
}

/** The heading of the "left out" list, or null when nothing was left out. */
export function skippedTitle(r: ComicUrlImportResult): string | null {
  if (!r.skipped_count) return null
  const shown = r.skipped.length < r.skipped_count ? ` (first ${r.skipped.length} shown)` : ''
  return `${plural(r.skipped_count, 'image')} left out${shown}`
}

// ---------------------------------------------------------------- SO10 review

export const REVIEW_WHY: Record<string, string> = {
  low_confidence: 'Baihe isn’t sure it found the right parts of the page, so nothing was saved yet.',
  asked: 'You asked to check the result before it is saved.',
  diagnostics: 'Sources diagnostics mode is on, so every result is shown here first.',
  follow: 'Baihe followed the next-chapter links. Nothing was saved yet: untick any page you don’t want, then import.',
}

export const reviewWhy = (why: string) => REVIEW_WHY[why] ?? REVIEW_WHY.low_confidence

export const REVIEW_FIRST_HELP =
  'Shows what Baihe found (and lets you correct it) before anything is saved. Baihe also does this by itself when it isn’t sure.'

export const REVIEW_NOTE =
  'Corrections change which parts of the page are used and can be saved as this site’s profile; the text and images themselves are never edited.'

export const BUCKET_TONE: Record<string, 'ok' | 'info' | 'warn' | 'bad'> = { HIGH: 'ok', MEDIUM: 'info', LOW: 'warn', FAILED: 'bad' }
export const bucketTone = (b: string | null | undefined) => BUCKET_TONE[b ?? ''] ?? 'warn'

const BUCKET_WORD: Record<string, string> = { HIGH: 'High', MEDIUM: 'Medium', LOW: 'Low', FAILED: 'Failed' }
export const bucketLabel = (b: string | null | undefined) => BUCKET_WORD[b ?? ''] ?? 'Unknown'

const FIELD_LABELS: Record<string, string> = {
  title: 'Title', author: 'Author', chapter_title: 'Chapter title', chapter_number: 'Chapter number',
  content: 'Chapter text', next_url: 'Next chapter', previous_url: 'Previous chapter', page_images: 'Page images',
  page_order: 'Page order', media_resources: 'Media',
}
export const fieldLabel = (f: string) => FIELD_LABELS[f] ?? f.replace(/_/g, ' ')

const ROLE_LABELS: Record<string, string> = {
  content: 'Page', cover: 'Cover', thumbnail: 'Thumbnail', ad: 'Ad', recommendation: 'Recommendation',
  icon: 'Icon', duplicate: 'Duplicate', other: 'Other',
}
export const roleLabel = (r: string) => ROLE_LABELS[r] ?? r.charAt(0).toUpperCase() + r.slice(1).replace(/_/g, ' ')

/** The corrections form for a novel review, as the server last showed it. */
export function novelForm(n: ExtractionNovel): Omit<NovelRerunRequest, 'revision'> {
  return {
    content_selector: n.content_selector ?? n.containers[0]?.selector ?? '',
    exclude_selectors: n.exclude_selectors,
    title_block: n.title_block,
    next_link: n.next_link,
    previous_link: n.previous_link,
    number_from: n.number_from,
  }
}

/** The leave-out options for the chosen container (only those are sent). */
export function exclusionsFor(n: ExtractionNovel, selector: string) {
  return n.containers.find((c) => c.selector === selector)?.exclusions ?? []
}

/** Change the container: leave-outs that don't belong to it are dropped. */
export function withContainer(form: Omit<NovelRerunRequest, 'revision'>, n: ExtractionNovel, selector: string) {
  const ok = new Set(exclusionsFor(n, selector).map((e) => e.selector))
  return { ...form, content_selector: selector, exclude_selectors: form.exclude_selectors.filter((s) => ok.has(s)) }
}

export const containerLabel = (c: { selector: string; chars: number; preview: string }) =>
  `${c.preview || c.selector} (${plural(c.chars, 'character')})`

/** The comic choices that differ from what the server shows, keyed by image id. */
export function changedImages(images: ExtractionImage[], edits: Record<number, { role: string; page: number }>): ImageChoice[] {
  const out: ImageChoice[] = []
  for (const img of images) {
    const e = edits[img.id]
    if (!e) continue
    const page = e.role === 'content' ? e.page : 0
    if (e.role !== img.role || page !== img.page) out.push({ id: img.id, role: e.role, page })
  }
  return out
}

/** Page numbers used more than once among the images marked as pages. */
export function duplicatePages(images: ExtractionImage[], edits: Record<number, { role: string; page: number }>): number[] {
  const seen = new Map<number, number>()
  for (const img of images) {
    const e = edits[img.id] ?? img
    if (e.role !== 'content' || e.page <= 0) continue
    seen.set(e.page, (seen.get(e.page) ?? 0) + 1)
  }
  return [...seen].filter(([, n]) => n > 1).map(([p]) => p).sort((a, b) => a - b)
}

const NONE_UNTICKED: ReadonlySet<number> = new Set()

export function importLabel(r: ExtractionReview, unticked: ReadonlySet<number> = NONE_UNTICKED): string {
  if (r.follow) return `Import ${plural(pickedPages(r.follow, unticked).length, 'page')}`
  if (r.content_type === 'novel') return 'Import this text'
  return `Import ${plural(r.comic?.page_count ?? 0, 'page')}`
}

export function canImport(r: ExtractionReview, unticked: ReadonlySet<number> = NONE_UNTICKED): boolean {
  if (r.follow) return pickedChars(r.follow, unticked) > 0
  return r.content_type === 'novel' ? (r.novel?.char_count ?? 0) > 0 : (r.comic?.page_count ?? 0) > 0
}

/** The pages to send with the import: the ticked ids of a followed import, else none (the whole review). */
export const importPages = (r: ExtractionReview, unticked: ReadonlySet<number>) =>
  r.follow ? pickedPages(r.follow, unticked) : null

export function reviewImportText(r: ReviewImportResult): string {
  if (r.content_type === 'novel' && (r.pages_imported ?? 1) > 1) {
    return `Added ${plural(r.pages_imported ?? 0, 'page')} (${plural(r.char_count ?? 0, 'character')}) to the drama’s novel text.`
  }
  if (r.content_type === 'novel') return `Added ${plural(r.char_count ?? 0, 'character')} to the drama’s novel text.`
  const skipped = r.skipped_count ? ` ${plural(r.skipped_count, 'image')} skipped (over a size limit or not PNG, JPEG or WebP).` : ''
  return `Added ${plural(r.pages_added ?? 0, 'page')} to the drama.${skipped}`
}

export const profileSavedText = (p: ProfileSaved) =>
  `Saved as profile v${p.version} for ${p.domain}; the next chapter from this site uses it` +
  (p.replaces ? ` (v${p.replaces} is kept and can be made active again).` : '.')

/** The page number after the highest one in use. */
export function nextPageNumber(images: ExtractionImage[], edits: Record<number, { role: string; page: number }>): number {
  let max = 0
  for (const img of images) {
    const e = edits[img.id] ?? img
    if (e.role === 'content') max = Math.max(max, e.page)
  }
  return Math.min(500, max + 1)
}
