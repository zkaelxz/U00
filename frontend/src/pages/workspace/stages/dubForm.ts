import type { DubConfig, DubPacingLine, DubRunRequest } from '../../../types/dub'

export interface DubForm {
  engine: string
  maxSpeedup: number
  maxSlowdown: number
  language: string
  keepBackground: boolean
}

export function initialDubForm(cfg: DubConfig): DubForm {
  return {
    engine: cfg.tts_engines[0]?.key ?? '',
    maxSpeedup: cfg.defaults?.max_speedup ?? 1.3,
    maxSlowdown: cfg.defaults?.max_slowdown ?? 0.85,
    language: cfg.narration_language,
    keepBackground: false,
  }
}

const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, n))

// Pacing limits only apply to timed (non-narration) dubs; keep_background is
// only ever sent when the server said it can be honoured.
export function buildDubRequest(cfg: DubConfig, form: DubForm): DubRunRequest {
  const body: DubRunRequest = {
    tts_engine: form.engine,
    keep_background: cfg.can_keep_background && form.keepBackground,
  }
  if (cfg.is_narration) body.narration_language = form.language
  else {
    body.max_speedup = clamp(form.maxSpeedup, 1.0, 2.0)
    body.max_slowdown = clamp(form.maxSlowdown, 0.5, 1.0)
  }
  return body
}

// Null when a dub can be started, otherwise the plain reason it cannot.
export function dubBlocker(cfg: DubConfig, form: DubForm): string | null {
  if (cfg.speakable_line_count === 0) return 'There is no text to speak yet.'
  if (!form.engine) return 'No voice engine is available.'
  return null
}

// One line for the Advanced summary: only what differs from the defaults.
export function dubAdvancedSummary(cfg: DubConfig, form: DubForm): string {
  const base = initialDubForm(cfg)
  const parts: string[] = []
  if (!cfg.is_narration) {
    if (form.maxSpeedup !== base.maxSpeedup) parts.push(`speed-up ${form.maxSpeedup}x`)
    if (form.maxSlowdown !== base.maxSlowdown) parts.push(`slow-down ${form.maxSlowdown}x`)
  }
  if (cfg.can_keep_background && form.keepBackground) parts.push('keep background music')
  return parts.length ? parts.join(' · ') : 'defaults'
}

// The settings a run would use, on one line under the primary button.
export function dubSettingsLine(cfg: DubConfig, form: DubForm): string {
  const engine = cfg.tts_engines.find((t) => t.key === form.engine)?.label ?? form.engine
  const parts = [engine]
  if (cfg.is_narration) parts.push(form.language)
  else parts.push(`speed ${form.maxSlowdown}x to ${form.maxSpeedup}x`)
  parts.push(cfg.can_keep_background && form.keepBackground ? 'background music kept' : 'no background music')
  return parts.join(' · ')
}

export function formatMs(ms: number | null | undefined): string {
  return typeof ms === 'number' && Number.isFinite(ms) ? `${Math.round(ms)} ms` : 'N/A'
}

export function formatFactor(f: number | null | undefined): string {
  return typeof f === 'number' && Number.isFinite(f) ? `${f.toFixed(2)}x` : 'N/A'
}

export function pacingSummary(counts: Record<string, number>): string {
  const parts = Object.entries(counts).map(([k, v]) => `${v} ${k}`)
  return parts.length ? parts.join(' · ') : 'No lines'
}

// Lines that did not fit are the ones worth looking at; fall back to all.
export function pacingRows(lines: DubPacingLine[]): DubPacingLine[] {
  const over = lines.filter((l) => l.status === 'overflow')
  return over.length ? over : lines
}

// A narration run that picked up an interrupted run's tagged batches reports
// "Resuming: N of M chunks already tagged..." (services/narration_service.py,
// Step 41); the Job panel then says how to start over instead.
export const NARRATION_RESUME_NOTE = 'Resuming an interrupted run. Use Start over to tag everything again.'

export function narrationResumeNote(message: string | null | undefined): string | null {
  return message?.startsWith('Resuming:') ? NARRATION_RESUME_NOTE : null
}

// Novel narration: an advisory note when some lines have no English yet
// (U01). What it means depends on the narration language: "translation"
// speaks the English, so those lines go silent; "original" speaks the source
// text, so only the exported bilingual subtitles are incomplete. Null when
// the count is 0, unknown, or the language is not one of the two.
export function untranslatedNarrationWarning(
  language: string,
  count: number | null | undefined,
): string | null {
  if (typeof count !== 'number' || !Number.isFinite(count) || count <= 0) return null
  const one = count === 1
  const n = `${count} ${one ? 'line has' : 'lines have'}`
  if (language === 'translation')
    return `${n} no English yet and will be silent in the narration. Translate ${one ? 'it' : 'them'} first.`
  if (language === 'original')
    return (
      `${n} no translation yet. Narration will still generate for ${one ? 'it' : 'them'} ` +
      `(it speaks the source text), but ${one ? 'its' : 'their'} exported subtitles will be missing ` +
      'the English half of the bilingual pair. Translate first if you want complete subtitles.'
    )
  return null
}
