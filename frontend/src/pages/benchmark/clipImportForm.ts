// The clip table's rows and what feeds them: the files picked or dropped, pasted text, an optional manifest.
// Pure, so the page stays thin. The server checks everything again; this only keeps the table honest.
import type { ClipNote, ClipRowPayload, SourceLanguage } from '../../types/benchmark'

export const MAX_CLIPS = 250
const AUDIO_NAME = /\.(mp3|wav|m4a|flac|ogg)$/i

export interface ClipRow {
  /** Stable across edits and removals. */
  key: number
  file: File
  title: string
  language: SourceLanguage
  tags: string
  transcript: string
  translation: string
}

interface NewRows {
  rows: ClipRow[]
  /** Names left out: not audio, already in the table, or over the clip limit. */
  skipped: string[]
}

/** Rows for the files that can be imported, after the ones already in the table. */
export function addFiles(existing: ClipRow[], files: File[], defaults: { language: SourceLanguage; title: string }, nextKey: number): NewRows {
  const names = new Set(existing.map((r) => r.file.name))
  const rows: ClipRow[] = []
  const skipped: string[] = []
  for (const file of files) {
    if (!AUDIO_NAME.test(file.name) || names.has(file.name) || existing.length + rows.length >= MAX_CLIPS) {
      skipped.push(file.name)
      continue
    }
    names.add(file.name)
    rows.push({
      key: nextKey + rows.length, file, title: defaults.title, language: defaults.language, tags: '', transcript: '', translation: '',
    })
  }
  return { rows, skipped }
}

/** One clip per non-blank line: the transcript, then a Tab and its translation. */
export function splitPaste(text: string): { transcript: string; translation: string }[] {
  return text
    .split(/\r?\n/)
    .filter((line) => line.trim())
    .map((line) => {
      const [transcript, ...rest] = line.split('\t')
      return { transcript: transcript.trim(), translation: rest.join('\t').trim() }
    })
}

/** Pasted lines fill the rows in order from the first; lines past the last row are counted, not kept. */
export function applyPaste(rows: ClipRow[], text: string): { rows: ClipRow[]; unused: number } {
  const lines = splitPaste(text)
  const next = rows.map((row, i) => (i < lines.length
    ? { ...row, transcript: lines[i].transcript, translation: lines[i].translation || row.translation }
    : row))
  return { rows: next, unused: Math.max(0, lines.length - rows.length) }
}

interface ManifestLine {
  audio: string
  language?: string
  transcript?: string
  translation?: string
  title?: string
  tags?: string[] | string
}

/** The manifest's lines (JSON, one per line, # comments skipped), or the line number that isn't one. */
export function parseManifest(text: string): { lines: ManifestLine[]; badLine: number | null } {
  const lines: ManifestLine[] = []
  const all = text.split(/\r?\n/)
  for (let i = 0; i < all.length; i++) {
    const raw = all[i].trim()
    if (!raw || raw.startsWith('#')) continue
    try {
      const obj = JSON.parse(raw) as unknown
      if (!obj || typeof obj !== 'object' || typeof (obj as ManifestLine).audio !== 'string') return { lines: [], badLine: i + 1 }
      lines.push(obj as ManifestLine)
    } catch {
      return { lines: [], badLine: i + 1 }
    }
  }
  return { lines, badLine: null }
}

const isLanguage = (v: unknown): v is SourceLanguage => v === 'zh' || v === 'ja' || v === 'ko'

/** Fills the rows whose file a manifest line names; names matching no picked file come back as `unmatched`. */
export function applyManifest(rows: ClipRow[], lines: ManifestLine[]): { rows: ClipRow[]; unmatched: string[] } {
  const base = (name: string) => name.replace(/\\/g, '/').split('/').pop() ?? name
  const byName = new Map(lines.map((l) => [base(l.audio), l]))
  const next = rows.map((row) => {
    const l = byName.get(row.file.name)
    if (!l) return row
    return {
      ...row,
      language: isLanguage(l.language) ? l.language : row.language,
      transcript: l.transcript ?? row.transcript,
      translation: l.translation ?? row.translation,
      title: l.title ?? row.title,
      tags: Array.isArray(l.tags) ? l.tags.join(', ') : (l.tags ?? row.tags),
    }
  })
  const picked = new Set(rows.map((r) => r.file.name))
  return { rows: next, unmatched: lines.map((l) => base(l.audio)).filter((n) => !picked.has(n)) }
}

const splitTags = (tags: string) => tags.split(/[,;，；]/).map((t) => t.trim()).filter(Boolean)

export function toPayload(row: ClipRow): ClipRowPayload {
  return {
    filename: row.file.name,
    language: row.language,
    transcript: row.transcript.trim(),
    ...(row.translation.trim() ? { translation: row.translation.trim() } : {}),
    ...(row.title.trim() ? { title: row.title.trim() } : {}),
    ...(row.tags.trim() ? { tags: splitTags(row.tags) } : {}),
  }
}

/** Why the table can't be saved yet, or null. */
export function importBlocker(setName: string, rows: ClipRow[]): string | null {
  if (rows.length === 0) return 'Add some audio files.'
  if (!setName.trim()) return 'Give the set a name.'
  const missing = rows.filter((r) => !r.transcript.trim())
  if (missing.length === 1) return `${missing[0].file.name} still needs a transcript.`
  if (missing.length) return `${missing.length} clips still need a transcript.`
  return null
}

/** The notes that name one file. */
export function notesFor(notes: ClipNote[], fileName: string): string[] {
  return notes.filter((n) => n.clip === fileName).map((n) => n.message)
}

/** The sentence under a finished import. */
export function importSummary(r: { set_name: string; clips: number; transcription_cases: number; translation_cases: number }): string {
  const cases = r.transcription_cases + r.translation_cases
  return `Added ${r.clips} ${r.clips === 1 ? 'clip' : 'clips'} to “${r.set_name}” (${cases} ${cases === 1 ? 'case' : 'cases'}: ${r.transcription_cases} transcription, ${r.translation_cases} translation).`
}
