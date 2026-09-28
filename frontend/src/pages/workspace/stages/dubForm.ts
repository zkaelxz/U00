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

export function formatMs(ms: number | null | undefined): string {
  return typeof ms === 'number' && Number.isFinite(ms) ? `${Math.round(ms)} ms` : 'n/a'
}

export function formatFactor(f: number | null | undefined): string {
  return typeof f === 'number' && Number.isFinite(f) ? `${f.toFixed(2)}x` : 'n/a'
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
