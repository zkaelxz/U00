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

/** The status sentence in the body. */
export function novelFileStatusLine(status: NovelFileStatus | null): string {
  if (!status) return 'Checking…'
  return status.present
    ? `Saved: ${status.char_count.toLocaleString()} characters (${size(status.size_bytes)}).`
    : 'Nothing saved yet.'
}
