// Reader API (/api/reader, route batch 2B). Reads need library.read, exports
// lines.read, writes lines.edit; AI tools need jobs.start plus the engine
// check (paid engines need engines.paid) and answer 429 when busy.

import type { TranslateEngine } from '../types/translate'
import type {
  CaptionTrack,
  ReaderAnswer,
  ReaderChatTurn,
  ReaderEngineFields,
  ReaderLookupRequest,
  ReaderLookupResult,
  ReaderMediaAvailability,
  ReaderNotes,
  ReaderOverview,
  ReaderPage,
  ReaderPageParams,
  ReaderProgress,
  ReaderRecap,
  ReaderRelationshipMap,
  ReaderRichExportResult,
  ReaderVocabList,
  ReaderWikiClearResult,
  ReaderWikiList,
  ReaderWikiUpdateResult,
  RichDeckDownload,
} from '../types/reader'
import { ApiError, getJson, postJson } from './client'
import type { ErrorInfo } from './types'

type Fetch = typeof fetch

// Same base as api/client.ts (relative by default; the Vite proxy forwards /api).
const BASE = (import.meta.env?.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

const root = (id: number) => `/api/reader/dramas/${id}`

function query(params: Record<string, string | number | boolean | null | undefined>): string {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') q.set(k, String(v))
  const s = q.toString()
  return s ? `?${s}` : ''
}

export function pagePath(id: number, p: ReaderPageParams): string {
  return `${root(id)}/page${query({ ...p })}`
}

export interface WikiScope {
  up_to_line_idx?: number
  entry_type?: string
}

// Plain links: the browser downloads these (Content-Disposition: attachment).
export const captionUrl = (id: number, track: CaptionTrack | string) =>
  `${BASE}${root(id)}/captions/${encodeURIComponent(track)}`
export const vocabCsvUrl = (id: number) => `${BASE}${root(id)}/vocab/export.csv`
export const vocabApkgUrl = (id: number) => `${BASE}${root(id)}/vocab/export.apkg`
export const wikiMarkdownUrl = (id: number, scope: WikiScope = {}) =>
  `${BASE}${root(id)}/wiki/export.md${query({ ...scope })}`
// The finished dub or narration track (media.stream).
export const dubTrackUrl = (id: number) => `${BASE}/api/dub/dramas/${id}/track`

// The engine ids the server's own _llm_engine rejects (translation-only).
const TRANSLATION_ONLY = new Set(['deepl', 'google', 'nllb', 'libretranslate'])

// Engines that can answer Reader AI requests: configured, and not translation-only.
export function readerEngines(all: TranslateEngine[]): TranslateEngine[] {
  return all.filter((e) => e.key_configured && !TRANSLATION_ONLY.has(e.name))
}

function headerFilename(resp: Response, fallback: string): string {
  const m = /filename="([^"]+)"/.exec(resp.headers.get('Content-Disposition') ?? '')
  return m ? m[1] : fallback
}

// The sentence-card deck is fetched rather than linked so the page can read
// X-Audio-Omitted (no media.stream: text-only deck) and X-Cards-Capped.
export async function downloadRichDeck(id: number, fetchImpl: Fetch = fetch): Promise<RichDeckDownload> {
  let resp: Response
  try {
    resp = await fetchImpl(`${BASE}${root(id)}/vocab/export.apkg?rich=true`, {
      headers: { Accept: 'application/octet-stream, application/json' },
    })
  } catch {
    throw new ApiError(0, { code: 'network_error', message: 'Could not reach the Baihe API. Is it running?' })
  }
  if (!resp.ok) {
    let info: ErrorInfo | undefined
    try {
      info = ((await resp.json()) as { error?: ErrorInfo } | null)?.error
    } catch {
      // not JSON
    }
    throw new ApiError(resp.status, info ?? { code: 'internal_error', message: `Request failed (${resp.status}).` })
  }
  const capped = resp.headers.get('X-Cards-Capped')
  return {
    blob: await resp.blob(),
    filename: headerFilename(resp, `drama_${id}_vocab_sentence.apkg`),
    audioOmitted: resp.headers.get('X-Audio-Omitted') === 'true',
    cardsCapped: capped && /^\d+$/.test(capped) ? Number(capped) : null,
  }
}

export const readerApi = {
  page: (id: number, p: ReaderPageParams, f?: Fetch) => getJson<ReaderPage>(pagePath(id, p), f),
  overview: (id: number, f?: Fetch) => getJson<ReaderOverview>(`${root(id)}/overview`, f),
  saveProgress: (id: number, page: number, chapter_size: number, f?: Fetch) =>
    postJson<ReaderProgress>(`${root(id)}/progress`, { page, chapter_size }, f),
  notes: (id: number, f?: Fetch) => getJson<ReaderNotes>(`${root(id)}/notes`, f),
  saveNotes: (id: number, notes: string, f?: Fetch) => postJson<ReaderNotes>(`${root(id)}/notes`, { notes }, f),
  media: (id: number, f?: Fetch) => getJson<ReaderMediaAvailability>(`${root(id)}/media`, f),
  lookup: (id: number, body: ReaderLookupRequest, f?: Fetch) =>
    postJson<ReaderLookupResult>(`${root(id)}/lookup`, body, f),
  vocab: (id: number, f?: Fetch) => getJson<ReaderVocabList>(`${root(id)}/vocab`, f),
  queueRich: (id: number, words: string[], queued = true, f?: Fetch) =>
    postJson<ReaderRichExportResult>(`${root(id)}/vocab/rich`, { words, queued }, f),
  who: (id: number, body: ReaderEngineFields & { name: string; up_to_line_idx?: number }, f?: Fetch) =>
    postJson<ReaderAnswer>(`${root(id)}/story/who`, body, f),
  explain: (id: number, body: ReaderEngineFields & { phrase: string; up_to_line_idx?: number }, f?: Fetch) =>
    postJson<ReaderAnswer>(`${root(id)}/story/explain`, body, f),
  recap: (id: number, body: ReaderEngineFields & { page: number; chapter_size: number }, f?: Fetch) =>
    postJson<ReaderRecap>(`${root(id)}/story/recap`, body, f),
  relationships: (id: number, body: ReaderEngineFields & { up_to_line_idx?: number }, f?: Fetch) =>
    postJson<ReaderRelationshipMap>(`${root(id)}/story/relationships`, body, f),
  wiki: (id: number, scope: WikiScope = {}, f?: Fetch) =>
    getJson<ReaderWikiList>(`${root(id)}/wiki${query({ ...scope })}`, f),
  updateWiki: (
    id: number,
    body: ReaderEngineFields & { up_to_line_idx?: number; from_line_idx: number },
    f?: Fetch,
  ) => postJson<ReaderWikiUpdateResult>(`${root(id)}/wiki/update`, body, f),
  clearWiki: (id: number, f?: Fetch) => postJson<ReaderWikiClearResult>(`${root(id)}/wiki/clear`, { confirm: true }, f),
  ask: (id: number, body: ReaderEngineFields & { question: string; chat_history: ReaderChatTurn[] }, f?: Fetch) =>
    postJson<ReaderAnswer>(`${root(id)}/ask`, body, f),
}
