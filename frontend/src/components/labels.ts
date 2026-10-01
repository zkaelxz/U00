// Badge helpers over the shared label maps in src/labels.ts (one source of
// truth for display labels). Unknown values fall back to a tidied form
// ("new_thing" -> "New thing").

import { ENGINE_LABELS, LANGUAGE_LABELS, LOCALE_LABELS, MEDIA_TYPE_LABELS, STATUS_LABELS } from '../labels'

export type LabelKind = 'status' | 'mediaType' | 'language' | 'engine' | 'locale'

const MAPS: Record<LabelKind, Record<string, string>> = {
  status: STATUS_LABELS,
  mediaType: MEDIA_TYPE_LABELS,
  language: LANGUAGE_LABELS,
  engine: ENGINE_LABELS,
  locale: LOCALE_LABELS,
}

// "kept_your_edit" / "STATIC-HTTP" -> "kept your edit" / "static http"
export function words(raw: string): string {
  return raw.replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim().toLowerCase()
}

// "not started" / "not_started" / "NOT-STARTED" -> "Not started"
export function tidy(raw: string): string {
  const s = words(raw)
  return s ? s[0].toUpperCase() + s.slice(1) : ''
}

// A free-form code with no label map (e.g. a source's "STATIC_HTTP"):
// tidied, or a dash when empty so a details row never looks blank.
export function humanizeValue(raw: string | null | undefined): string {
  return raw ? tidy(String(raw)) : '—'
}

export function humanize(kind: LabelKind, raw: string | null | undefined): string {
  if (raw == null || raw === '') return ''
  return MAPS[kind][raw.toLowerCase()] ?? tidy(raw)
}

export type BadgeTone = 'neutral' | 'accent' | 'ok' | 'warn' | 'bad' | 'info'

// Pipeline status -> badge colour: done states green, in-progress accent/info.
const STATUS_TONE: Record<string, BadgeTone> = {
  'not started': 'neutral',
  aligned: 'info',
  translated: 'accent',
  dubbed: 'accent',
  exported: 'ok',
  running: 'info',
  queued: 'neutral',
  done: 'ok',
  failed: 'bad',
  error: 'bad',
  cancelled: 'warn',
}

export function statusTone(raw: string | null | undefined): BadgeTone {
  return STATUS_TONE[words(raw ?? '')] ?? 'neutral'
}
