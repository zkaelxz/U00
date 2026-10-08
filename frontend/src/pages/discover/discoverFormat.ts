/*
 * Discover page: pure helpers (unit-tested in discoverFormat.test.ts).
 * Limits mirror services/discover_lookup_service.py and bulk_import.py.
 */
import { humanize } from '../../components/labels'
import type { TranslateEngine } from '../../types/translate'
import type { BulkEntry, KnownTitle, KnownTitleCreate } from '../../types/discover'

export const MAX_BULK_URLS = 10 // discover_lookup_service.MAX_BULK_URLS
// Owner decision: the Catalogue tab is hidden while unused. Data and API routes stay; set true to bring it back.
export const CATALOGUE_TAB_ENABLED = false

export const MAX_PAGINATE_PAGES = 50 // bulk_import.MAX_PAGINATE_PAGES
const MAX_COMMIT_ENTRIES = 500

export const LANGUAGES = [
  { code: 'zh', label: 'Chinese' },
  { code: 'ja', label: 'Japanese' },
  { code: 'ko', label: 'Korean' },
]
export const TITLE_MEDIA_TYPES = ['novel', 'audio_drama', 'manhwa', 'manga', 'manhua', 'game']
// search-links `format` (discover_catalog_service.SEARCH_LINK_FORMATS)
export const LINK_FORMATS = ['audio_drama', 'novel', 'manhua', 'manhwa', 'manga']
export const PLATFORM_TYPES = ['novel', 'manhwa', 'manga', 'manhua', 'audio_drama']
export const TARGET_LANGUAGES = ['English', 'Vietnamese', 'Chinese', 'Japanese', 'Korean']

// Engines the server accepts here (translate_engines supports_reference).
const DISCOVER_ENGINE_IDS = new Set(['claude', 'deepseek', 'gemini', 'openai', 'ollama'])

/** Picker entries: engines the Discover routes accept that have a key (or need none). */
export function discoverEngines(all: TranslateEngine[]): TranslateEngine[] {
  return all.filter((e) => DISCOVER_ENGINE_IDS.has(e.name) && e.key_configured)
}

export function mediaLabel(type: string | null | undefined): string {
  return humanize('mediaType', type) || '—'
}

/** True if the text has a CJK ideograph (the server's own "already Chinese" test). */
export function hasChinese(text: string): boolean {
  return /[一-鿿]/.test(text)
}

/** "Author · tags" for a catalogue card (type and language are badges). */
export function titleMeta(t: KnownTitle): string {
  return [t.author || 'Unknown author', t.tags || ''].filter(Boolean).join(' · ')
}

/** The count line under the catalogue search. */
export function catalogCount(shown: number, total: number, q: string): string {
  if (total === 0) return 'Your catalogue is empty.'
  if (shown === 0 && q) return `No match for “${q}” among your ${total} saved title${total === 1 ? '' : 's'}.`
  return `${shown} of ${total} saved title${total === 1 ? '' : 's'}${q ? ` matching “${q}”` : ''}`
}

/**
 * Page URLs from a pattern with a literal "{page}" (like bulk_import.paginate_urls:
 * plain replacement, range capped). Returns [] for a pattern without {page}
 * or a range that is not whole numbers >= 1.
 */
export function paginateUrls(pattern: string, start: number, end: number): string[] {
  if (!pattern.includes('{page}') || !Number.isInteger(start) || !Number.isInteger(end) || start < 1 || end < start) {
    return []
  }
  const last = Math.min(end, start + MAX_PAGINATE_PAGES - 1)
  const out: string[] = []
  for (let p = start; p <= last; p++) out.push(pattern.split('{page}').join(String(p)))
  return out
}

/** One URL per non-blank line, trimmed, duplicates dropped. */
export function parseUrlList(text: string): string[] {
  return [...new Set(text.split(/\r?\n/).map((u) => u.trim()).filter(Boolean))]
}

export function isHttpUrl(url: string): boolean {
  return /^https?:\/\/\S+$/i.test(url.trim())
}

/** Why the listing URLs can't be sent yet, or null. */
export function bulkUrlsProblem(urls: string[]): string | null {
  if (urls.length === 0) return 'Add at least one listing page URL.'
  if (urls.length > MAX_BULK_URLS) return `At most ${MAX_BULK_URLS} pages per run (you have ${urls.length}).`
  const bad = urls.find((u) => !isHttpUrl(u))
  return bad ? `Not a web address: ${bad.slice(0, 60)}` : null
}

/** The body for bulk-commit: the ticked entries, with only the fields it accepts. */
export function commitEntries(entries: BulkEntry[], included: ReadonlySet<number>): BulkEntry[] {
  return entries
    .filter((_, i) => included.has(i))
    .slice(0, MAX_COMMIT_ENTRIES)
    .map((e) => ({
      title: e.title,
      author: e.author || '',
      tags: e.tags || '',
      source_url: e.source_url || '',
      has_audio_drama: e.has_audio_drama === true,
      language: e.language || 'zh',
      ...(e.entry_id ? { entry_id: e.entry_id } : {}),
    }))
}

export const EMPTY_TITLE: KnownTitleCreate = {
  title_original: '',
  title_en: '',
  author: '',
  tags: '',
  summary_en: '',
  source_name: 'manual',
  source_url: '',
  language: 'zh',
  media_type: 'novel',
}

/**
 * Fills the add-a-title form from an import suggestion (import-suggestion's
 * fields: title_en, title_zh, author, summary, ...). Fields the suggestion
 * leaves blank keep what the form already had.
 */
export function applySuggestion(form: KnownTitleCreate, s: Record<string, string>, url: string): KnownTitleCreate {
  const pick = (v: string | undefined, cur: string) => (v && v.trim() ? v.trim() : cur)
  return {
    ...form,
    title_original: pick(s.title_zh, form.title_original),
    title_en: pick(s.title_en, form.title_en),
    author: pick(s.author, form.author),
    summary_en: pick(s.summary, form.summary_en),
    source_url: url.trim() || form.source_url,
    source_name: 'url',
    language: s.title_zh && s.title_zh.trim() ? 'zh' : form.language,
  }
}

/** Why the add-a-title form can't be saved yet, per field; {} when it can. */
export function titleFormProblems(form: KnownTitleCreate): { title?: string; url?: string } {
  const out: { title?: string; url?: string } = {}
  if (!form.title_original.trim()) out.title = 'Enter the title in its original language.'
  if (form.source_url.trim() && !isHttpUrl(form.source_url)) out.url = 'The source URL must start with http:// or https://.'
  return out
}

/** The drama id from an import 409 ("already in your Library"), if any. */
export function existingDramaId(err: unknown): number | null {
  const e = err as { status?: number; details?: unknown } | null
  if (!e || e.status !== 409) return null
  const id = (e.details as { drama_id?: unknown } | undefined)?.drama_id
  return typeof id === 'number' && Number.isInteger(id) && id > 0 ? id : null
}

/** An external link's href: only http(s) addresses, else null (render as text). */
export function safeHref(url: string | null | undefined): string | null {
  return url && isHttpUrl(url) ? url.trim() : null
}

/** Link text for a title's source: its site name, else the address's host. */
export function sourceLabel(name: string | null | undefined, url: string): string {
  if (name && name !== 'manual' && name !== 'url') return name
  try {
    return new URL(url).hostname || url
  } catch {
    return url
  }
}
