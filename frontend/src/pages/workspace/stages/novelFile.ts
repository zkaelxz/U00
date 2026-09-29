// Pure helpers for NovelFilePanel: which file kinds each control accepts
// (mirrors services/novel_files_service.py REFERENCE_EXTENSIONS /
// RAW_NOVEL_EXTENSIONS) and the status wording.
import type { NovelFileStatus } from '../../../types/novelFiles'

export type NovelFileKind = 'reference' | 'raw'

export const NOVEL_FILE_EXTENSIONS: Record<NovelFileKind, readonly string[]> = {
  reference: ['.txt', '.md'],
  raw: ['.txt', '.md', '.epub'],
}

/** A problem with the picked file's name, or null if the type is accepted. */
export function checkNovelFile(kind: NovelFileKind, name: string): string | null {
  const base = name.split(/[\\/]/).pop() ?? ''
  const dot = base.lastIndexOf('.')
  const ext = dot > 0 ? base.slice(dot).toLowerCase() : ''
  const allowed = NOVEL_FILE_EXTENSIONS[kind]
  return allowed.includes(ext) ? null : `${base || 'That file'} is not a ${allowed.join(', ')} file.`
}

function size(bytes: number): string {
  return bytes < 1024 ? `${bytes} bytes` : `${Math.round(bytes / 1024).toLocaleString()} KB`
}

/** One short line for the Section summary (visible while closed). */
export function novelFileSummary(status: NovelFileStatus | null): string {
  if (!status) return 'checking'
  return status.present ? `${status.char_count.toLocaleString()} chars` : 'none saved'
}

// services/novel_files_service.py MAX_TEXT_CHARS (counted after trimming).
export const MAX_NOVEL_TEXT_CHARS = 10_000_000

/** "1,234 characters" for the paste box: what the server will store (trimmed). */
export function pastedCount(text: string): string {
  const n = text.trim().length
  return `${n.toLocaleString()} ${n === 1 ? 'character' : 'characters'}`
}

/** Why the pasted text can't be saved, or null. Empty is just "nothing yet". */
export function pasteProblem(text: string): string | null {
  return text.trim().length > MAX_NOVEL_TEXT_CHARS
    ? `Too long: the limit is ${MAX_NOVEL_TEXT_CHARS.toLocaleString()} characters.`
    : null
}

/** The status sentence in the body. */
export function novelFileStatusLine(status: NovelFileStatus | null): string {
  if (!status) return 'Checking…'
  return status.present
    ? `Saved: ${status.char_count.toLocaleString()} characters (${size(status.size_bytes)}).`
    : 'Nothing saved yet.'
}
