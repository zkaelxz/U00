// Pure helpers for SubtitleImport: client-side file check and the wording of the preview.
import type { SubtitleImportPreview } from '../../../types/subtitleImport'

export const SUBTITLE_EXTENSIONS = ['.srt', '.vtt', '.ass', '.ssa', '.lrc']
// Same cap as the server (subtitle_parse.MAX_FILE_BYTES); the server stays the authority.
export const MAX_SUBTITLE_BYTES = 2 * 1024 * 1024

// Value is the Python codec name the server accepts; '' lets it detect.
export const ENCODING_CHOICES: { value: string; label: string }[] = [
  { value: '', label: 'Detect automatically' },
  { value: 'utf-8', label: 'UTF-8' },
  { value: 'utf-16', label: 'UTF-16' },
  { value: 'gb18030', label: 'Chinese (GB18030)' },
  { value: 'big5', label: 'Chinese (Big5)' },
  { value: 'cp932', label: 'Japanese (Shift-JIS)' },
  { value: 'cp949', label: 'Korean (EUC-KR)' },
  { value: 'cp1252', label: 'Western (Windows-1252)' },
]

export function checkSubtitleFile(name: string, size: number): string | null {
  const dot = name.lastIndexOf('.')
  const ext = dot >= 0 ? name.slice(dot).toLowerCase() : ''
  if (!SUBTITLE_EXTENSIONS.includes(ext)) return 'Pick an SRT, VTT, ASS, SSA or LRC file.'
  if (size > MAX_SUBTITLE_BYTES) return `That file is too large (at most ${MAX_SUBTITLE_BYTES / (1024 * 1024)} MB).`
  if (size === 0) return 'That file is empty.'
  return null
}

const LANGUAGE_NAMES: Record<string, string> = {
  zh: 'Chinese', ja: 'Japanese', ko: 'Korean', latin: 'Latin-script text',
}

export function fileSummary(p: SubtitleImportPreview): string {
  const parts = [
    `${p.format.toUpperCase()}`,
    `${p.cue_count} cue${p.cue_count === 1 ? '' : 's'}`,
    `read as ${p.encoding}${p.encoding_guessed ? ' (guessed)' : ''}`,
  ]
  const lang = p.detected_language ? LANGUAGE_NAMES[p.detected_language] : null
  if (lang) parts.push(lang)
  return parts.join(' · ')
}

/** What the import would do, in one sentence. */
export function impactLine(p: SubtitleImportPreview): string {
  if (p.blocked_reason) return p.blocked_reason
  if (p.mode === 'source') {
    return p.replaces_lines > 0
      ? `Replaces this title's ${p.replaces_lines} line${p.replaces_lines === 1 ? '' : 's'} with ${p.cue_count} from the file.`
      : `Adds ${p.cue_count} line${p.cue_count === 1 ? '' : 's'}, with the file's times.`
  }
  const one = p.unmatched_cues === 1
  const unmatched = p.unmatched_cues > 0 ? ` ${p.unmatched_cues} cue${one ? ' matches' : 's match'} no line.` : ''
  return `Puts text on ${p.matched_lines} of ${p.existing_line_count} lines, matched by time.${unmatched}`
}

/** The confirmation the import needs before it may run, or null. */
export function needsConfirm(p: SubtitleImportPreview): 'replace' | 'overwrite' | null {
  if (p.mode === 'source') return p.replaces_lines > 0 ? 'replace' : null
  return p.overwrites > 0 ? 'overwrite' : null
}

export function confirmLabel(p: SubtitleImportPreview): string {
  return p.mode === 'source'
    ? `Replace the ${p.replaces_lines} current line${p.replaces_lines === 1 ? '' : 's'} (saved to history first)`
    : `Overwrite ${p.overwrites} existing translation${p.overwrites === 1 ? '' : 's'} (saved to history first)`
}
