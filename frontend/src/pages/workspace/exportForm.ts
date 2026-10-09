// Pure form logic for the Export stage (no React): turns the ASS form into
// an API request, honouring the caps the API enforces.
import type {
  AssExportRequest,
  AssStyleOptions,
  AssStyleOverrides,
  MediaKind,
  SubtitleField,
} from '../../types/export'
import { readSectionOpen, type StorageLike } from '../../components/sectionStorage'

export const MAX_SPEAKER_COLORS = 200
const MAX_LABEL_CHARS = 100
const MAX_WRAP = 200

export interface AssForm {
  field: SubtitleField
  preset: string
  font: string
  size: string
  outlineWidth: string
  shadow: string
  primary: string
  outline: string
  bold: '' | 'yes' | 'no'
  italic: '' | 'yes' | 'no'
  alignment: string
  sfxAlignment: string
  notesAlignment: string
  speakerColors: string
  perSpeakerColors: boolean
  includeNotes: boolean
  notesAsSeparateLine: boolean
  wrapEn: string
  wrapSource: string
  baseName: string
}

export const emptyAssForm = (preset: string): AssForm => ({
  field: 'en', preset, font: '', size: '', outlineWidth: '', shadow: '', primary: '', outline: '',
  bold: '', italic: '', alignment: '', sfxAlignment: '', notesAlignment: '', speakerColors: '',
  perSpeakerColors: true, includeNotes: false, notesAsSeparateLine: false, wrapEn: '', wrapSource: '', baseName: '',
})

const HEX = /^#[0-9A-Fa-f]{6}$/

const formKey = (dramaId: number) => `baihe.export.style.${dramaId}`

// The Export stage's style form is kept per viewer so the Review burn preview
// can render with the same style. Storage may be missing or throw.
export function saveAssForm(dramaId: number, form: AssForm): void {
  try {
    window.localStorage.setItem(formKey(dramaId), JSON.stringify(form))
  } catch {
    // ignore
  }
}

export function loadAssForm(dramaId: number): AssForm | null {
  try {
    const raw = window.localStorage.getItem(formKey(dramaId))
    if (!raw) return null
    const parsed = JSON.parse(raw) as Record<string, unknown> | null
    if (!parsed || typeof parsed !== 'object') return null
    const out: Record<string, unknown> = { ...emptyAssForm('') }
    for (const [k, v] of Object.entries(out)) {
      if (typeof parsed[k] === typeof v) out[k] = parsed[k]
    }
    return out as unknown as AssForm
  } catch {
    return null
  }
}

// Blank means "not set"; otherwise an integer from min to max.
export function parseWrap(text: string): { value?: number; error?: string } {
  const t = text.trim()
  if (!t) return {}
  const n = Number(t)
  if (!Number.isInteger(n) || n < 1 || n > MAX_WRAP) {
    return { error: `Line wrapping must be a whole number from 1 to ${MAX_WRAP}, or blank.` }
  }
  return { value: n }
}

// One "Speaker name = #RRGGBB" per line.
export function parseSpeakerColors(text: string): { colors?: Record<string, string>; error?: string } {
  const colors: Record<string, string> = {}
  for (const raw of text.split('\n')) {
    const row = raw.trim()
    if (!row) continue
    const at = row.lastIndexOf('=')
    const name = at < 0 ? '' : row.slice(0, at).trim()
    const color = at < 0 ? '' : row.slice(at + 1).trim()
    if (!name || !HEX.test(color)) {
      return { error: `Speaker colours need one "Name = #RRGGBB" per line (problem: "${row.slice(0, 40)}").` }
    }
    if (name.length > MAX_LABEL_CHARS) {
      return { error: `A speaker name is longer than ${MAX_LABEL_CHARS} characters.` }
    }
    colors[name] = color
  }
  const n = Object.keys(colors).length
  if (n > MAX_SPEAKER_COLORS) return { error: `At most ${MAX_SPEAKER_COLORS} speaker colours (you have ${n}).` }
  return n ? { colors } : {}
}

function intField(text: string, label: string, range: number[]): { value?: number; error?: string } {
  const t = text.trim()
  if (!t) return {}
  const n = Number(t)
  if (!Number.isInteger(n) || n < range[0] || n > range[1]) {
    return { error: `${label} must be a whole number from ${range[0]} to ${range[1]}.` }
  }
  return { value: n }
}

export function buildAssRequest(
  form: AssForm,
  opts: AssStyleOptions,
): { request: AssExportRequest; error?: undefined } | { request?: undefined; error: string } {
  const style: AssStyleOverrides = {}
  for (const [key, text, label, range] of [
    ['size', form.size, 'Font size', opts.size_range],
    ['outline_width', form.outlineWidth, 'Outline width', opts.outline_width_range],
    ['shadow', form.shadow, 'Shadow', opts.shadow_range],
  ] as const) {
    const r = intField(text, label, range)
    if (r.error) return { error: r.error }
    if (r.value !== undefined) style[key] = r.value
  }
  for (const [key, text, label] of [
    ['primary', form.primary, 'Text colour'],
    ['outline', form.outline, 'Outline colour'],
  ] as const) {
    const t = text.trim()
    if (!t) continue
    if (!HEX.test(t)) return { error: `${label} must look like #RRGGBB.` }
    style[key] = t
  }
  if (form.font.trim()) {
    if (form.font.length > 100 || [...form.font].some((c) => c < ' ' || c === '\x7f')) {
      return { error: 'The font name is not valid.' }
    }
    style.font = form.font.trim()
  }
  if (form.bold) style.bold = form.bold === 'yes'
  if (form.italic) style.italic = form.italic === 'yes'
  for (const [key, value] of [
    ['alignment', form.alignment],
    ['sfx_alignment', form.sfxAlignment],
    ['notes_alignment', form.notesAlignment],
  ] as const) {
    if (value) style[key] = value
  }

  const colors = parseSpeakerColors(form.speakerColors)
  if (colors.error) return { error: colors.error }
  const wrapEn = parseWrap(form.wrapEn)
  const wrapSource = parseWrap(form.wrapSource)
  if (wrapEn.error || wrapSource.error) return { error: (wrapEn.error ?? wrapSource.error) as string }

  const request: AssExportRequest = {
    field: form.field,
    preset: form.preset,
    per_speaker_colors: form.perSpeakerColors,
    include_notes: form.includeNotes,
    notes_as_separate_line: form.includeNotes && form.notesAsSeparateLine,
  }
  if (Object.keys(style).length) request.style = style
  if (colors.colors) request.speaker_colors = colors.colors
  if (wrapEn.value !== undefined) request.wrap_chars_en = wrapEn.value
  if (wrapSource.value !== undefined) request.wrap_chars_source = wrapSource.value
  return { request }
}

export const MAX_BASE_NAME = 100

// Download name for a generated subtitle file, saved client-side (Blob +
// <a download>). A name the user typed wins; blank means the server's
// Content-Disposition name (title, episode, language), and only when the
// response carried none, drama_<id>_<field>.
export function exportFilename(
  base: string, dramaId: number, field: SubtitleField, ext: string, serverName: string | null = null,
): string {
  const clean = [...base.trim()]
    .map((c) => (c < ' ' || c === '\x7f' || '<>:"/\\|?*'.includes(c) ? '_' : c))
    .join('')
    .slice(0, MAX_BASE_NAME)
    .replace(/[.\s]+$/, '')
  if (!clean && serverName) return serverName
  return `${clean || `drama_${dramaId}_${field}`}.${ext}`
}

interface ResolvedAssStyle {
  font: string
  size: number
  bold: boolean
  italic: boolean
  primary: string
  outline: string
  outlineWidth: number
  shadow: number
  alignment: string
}

// Preset values with the form's valid overrides applied, for the local preview
// only (the server applies the real style). Invalid overrides fall back to the preset.
export function resolveAssStyle(form: AssForm, opts: AssStyleOptions): ResolvedAssStyle {
  const p = (opts.presets[form.preset] ?? opts.presets[opts.default_preset] ?? {}) as Record<string, unknown>
  const str = (v: unknown, d: string) => (typeof v === 'string' && v ? v : d)
  const num = (v: unknown, d: number) => (typeof v === 'number' ? v : d)
  const int = (text: string, range: number[], d: number) => intField(text, '', range).value ?? d
  const hex = (text: string, d: string) => (HEX.test(text.trim()) ? text.trim() : d)
  return {
    font: form.font.trim() || str(p.font, 'Arial'),
    size: int(form.size, opts.size_range, num(p.size, 24)),
    bold: form.bold ? form.bold === 'yes' : p.bold === true,
    italic: form.italic ? form.italic === 'yes' : p.italic === true,
    primary: hex(form.primary, str(p.primary, '#FFFFFF')),
    outline: hex(form.outline, str(p.outline, '#000000')),
    outlineWidth: int(form.outlineWidth, opts.outline_width_range, num(p.outline_width, 2)),
    shadow: int(form.shadow, opts.shadow_range, num(p.shadow, 0)),
    alignment: form.alignment || str(p.alignment, 'bottom-center'),
  }
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

// Which media export blocks start open: the subtitle-track video is the one used most.
const MEDIA_BLOCK_DEFAULT_OPEN: Partial<Record<MediaKind, boolean>> = { softsub_video: true }

export function mediaBlockStorageKey(kind: MediaKind): string {
  return `export.media.${kind}`
}

/** The remembered open state of a media block, else its default. */
export function mediaBlockOpen(storage: StorageLike | null, kind: MediaKind): boolean {
  return readSectionOpen(storage, mediaBlockStorageKey(kind), MEDIA_BLOCK_DEFAULT_OPEN[kind] === true)
}
