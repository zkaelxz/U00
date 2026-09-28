// Pure form logic for the Export stage (no React): turns the ASS form into
// an API request, honouring the caps the API enforces.
import type {
  AssExportRequest,
  AssStyleOptions,
  AssStyleOverrides,
  SubtitleField,
} from '../../types/export'

export const MAX_SPEAKER_COLORS = 200
export const MAX_LABEL_CHARS = 100
export const MAX_WRAP = 200

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
}

export const emptyAssForm = (preset: string): AssForm => ({
  field: 'en', preset, font: '', size: '', outlineWidth: '', shadow: '', primary: '', outline: '',
  bold: '', italic: '', alignment: '', sfxAlignment: '', notesAlignment: '', speakerColors: '',
  perSpeakerColors: true, includeNotes: false, notesAsSeparateLine: false, wrapEn: '', wrapSource: '',
})

const HEX = /^#[0-9A-Fa-f]{6}$/

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

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}
