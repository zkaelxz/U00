// Pure helpers for the Workspace preamble/Source parity panels: credits and
// their romanized forms (P13), cover art (P14), the EPUB chapter range (S13).
import type { DramaDetail } from '../../api/types'
import type { NovelAttachResult } from '../../types/workspace'

const CREDITS = [
  ['author', 'Author'],
  ['studio', 'Studio'],
  ['director', 'Director'],
  ['voice_actors', 'Cast'],
] as const

/** 'Romanized (原文)', or whichever exists (translation_guide.format_bilingual_credit). */
export function bilingualCredit(original: string | null, romanized: string | null): string {
  const o = (original ?? '').trim()
  const r = (romanized ?? '').trim()
  if (o && r && o !== r) return `${r} (${o})`
  return r || o
}

export type CreditRow = { key: string; label: string; text: string; romanized: boolean }

/** One row per credit that is set, shown bilingually once romanized. */
export function creditRows(d: Pick<DramaDetail,
  'author' | 'studio' | 'director' | 'voice_actors' |
  'author_romanized' | 'studio_romanized' | 'director_romanized' | 'voice_actors_romanized'>): CreditRow[] {
  const rows: CreditRow[] = []
  for (const [key, label] of CREDITS) {
    const rom = d[`${key}_romanized`]
    const text = bilingualCredit(d[key], rom)
    if (text) rows.push({ key, label, text, romanized: !!(rom ?? '').trim() })
  }
  return rows
}

/** Whether any original credit is set (romanizing needs one). */
export const hasCredits = (d: Pick<DramaDetail, 'author' | 'studio' | 'director' | 'voice_actors'>) =>
  CREDITS.some(([k]) => (d[k] ?? '').trim() !== '')

// Server caps (services/cover_art_service.py).
export const COVER_MAX_BYTES = 10 * 1024 * 1024
export const COVER_TYPES = ['image/png', 'image/jpeg', 'image/webp']
export const COVER_ACCEPT = '.png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp'

/** Why this file can't be a cover, or null. The server checks the bytes again. */
export function coverFileProblem(file: Pick<File, 'type' | 'size' | 'name'> | null): string | null {
  if (!file) return null
  const ext = file.name.toLowerCase().match(/\.(png|jpe?g|webp)$/)
  if (!COVER_TYPES.includes(file.type) && !ext) return 'Choose a PNG, JPEG or WebP image.'
  if (file.size > COVER_MAX_BYTES) return 'That image is larger than 10 MB.'
  if (file.size === 0) return 'That file is empty.'
  return null
}

/**
 * The EPUB chapter range from two text boxes (blank = first / last). Returns
 * the numbers to send, or a problem sentence.
 */
export function epubRange(fromText: string, toText: string): { from?: number; to?: number } | { problem: string } {
  const parse = (t: string) => (t.trim() === '' ? undefined : Number(t.trim()))
  const from = parse(fromText)
  const to = parse(toText)
  for (const v of [from, to]) {
    if (v !== undefined && (!Number.isInteger(v) || v < 1)) return { problem: 'Chapter numbers are whole numbers from 1.' }
  }
  if (from !== undefined && to !== undefined && from > to) return { problem: 'The first chapter comes after the last.' }
  return { from, to }
}

/** "Attached 12,345 characters (chapters 2–5 of 40)." */
export function attachNotice(r: NovelAttachResult): string {
  const chars = `Attached ${r.char_count.toLocaleString()} characters`
  if (r.epub_chapters && r.chapter_from && r.chapter_to) {
    const all = r.chapter_from === 1 && r.chapter_to === r.epub_chapters
    const span = r.chapter_from === r.chapter_to ? `chapter ${r.chapter_from}` : `chapters ${r.chapter_from}–${r.chapter_to}`
    return all ? `${chars} (all ${r.epub_chapters} chapters).` : `${chars} (${span} of ${r.epub_chapters}).`
  }
  return `${chars}.`
}
