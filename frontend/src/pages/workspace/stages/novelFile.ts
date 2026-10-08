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

// services/novel_attach_service.py MAX_EPUB_BYTES (also the cap for a raw-novel .epub).
export const MAX_NOVEL_EPUB_BYTES = 50 * 1024 * 1024

/** Why an .epub is too big to upload, or null (other file kinds are not checked here). */
export function epubSizeProblem(file: { name: string; size: number }): string | null {
  const base = file.name.split(/[\\/]/).pop() ?? ''
  return base.toLowerCase().endsWith('.epub') && file.size > MAX_NOVEL_EPUB_BYTES
    ? `${base} is larger than the ${MAX_NOVEL_EPUB_BYTES / 1024 / 1024} MB limit for EPUB files.`
    : null
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

export type NovelPresence = { text: boolean; raw: boolean }
export type NovelSections = 'full' | 'optional' | 'hidden'

/**
 * How much of the novel UI a title gets in the Source stage. Novels, comics
 * and novel narration work from novel text, so they always get the full
 * panels; any other title gets them once text or a raw novel is attached.
 * Until then a streamer VOD has no use for a novel and sees nothing, and the
 * rest (audio dramas and so on) can still attach one from a single optional
 * row. `presence` is null while it is being read.
 */
export function novelSections(
  kind: 'audio' | 'novel' | 'comic',
  mediaType: string | null | undefined,
  contentMode: string | null | undefined,
  presence: NovelPresence | null,
): NovelSections {
  if (kind !== 'audio' || mediaType === 'novel_narration' || contentMode === 'novel_narration') return 'full'
  if (presence && (presence.text || presence.raw)) return 'full'
  return mediaType === 'streamer_vod' || contentMode === 'streamer_vod' ? 'hidden' : 'optional'
}
