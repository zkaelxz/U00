// Pure helpers for the Review stage's results and checks (ReviewFindings,
// ReviewChecks, LineOrigin) and the AI jobs' start options. Lines are
// linked by permanent id; idx is shown only as lineNumber(idx).
import { humanize } from '../../../../components/labels'
import { lineNumber } from '../../../../lineNumber'
import type {
  Coverage,
  CoverageEntry,
  EmotionSummary,
  PacingFlag,
  ReviewJobBody,
  ReviewJobKind,
  ReviewLine,
} from '../../../../types/review'

// ---- fix-flagged start options ----

// The chosen engine's label, else "<default> engine": "Claude", "Gemini engine".
function engineName(engine: string, defaultEngine: string): string {
  return engine ? humanize('engine', engine) : `${defaultEngine ? humanize('engine', defaultEngine) : 'Default'} engine`
}

export interface FixForm {
  engine: string // '' = the server's default engine
  model: string // '' = the engine's default model
  cap: string // '' = no per-job cap
}

export const EMPTY_FIX_FORM: FixForm = { engine: '', model: '', cap: '' }

/** The request body (only fields the user set) or a plain problem message. */
export function fixFlaggedBody(f: FixForm): ReviewJobBody | string {
  const body: ReviewJobBody = {}
  const engine = f.engine.trim()
  const model = f.model.trim()
  const cap = f.cap.trim()
  if (engine) body.engine = engine
  if (model) body.model = model
  if (cap !== '') {
    const n = Number(cap)
    if (!Number.isFinite(n) || n < 0) return 'Cost cap must be a number of dollars, 0 or more.'
    body.job_cost_cap_usd = n
  }
  return body
}

// R50/R33: engine and model for the other AI checks (consistency, emotion,
// notes, flag), plus emotion's audio-cues toggle. Unset fields are left out
// so the server's defaults apply (the drama's engine; cues on with audio).
export interface CheckForm {
  engine: string // '' = the drama's engine
  model: string // '' = the engine's default model
  audioCues: boolean | null // null = the server default
}

export const EMPTY_CHECK_FORM: CheckForm = { engine: '', model: '', audioCues: null }

// bulk (R49): sent only when true, and never for fix-flagged. The caller
// checks the engine first (reviewBulk.ts reviewStartBody).
export function checkJobBody(kind: ReviewJobKind, f: CheckForm, bulk = false): ReviewJobBody {
  const body: ReviewJobBody = {}
  const engine = f.engine.trim()
  const model = f.model.trim()
  if (engine) body.engine = engine
  if (model) body.model = model
  if (kind === 'emotion' && f.audioCues !== null) body.use_audio_cues = f.audioCues
  if (bulk && kind !== 'fix-flagged') body.bulk = true
  return body
}

export function checkFormSummary(f: CheckForm, defaultEngine: string, hasAudio: boolean): string {
  const parts = [engineName(f.engine, defaultEngine)]
  if (f.model) parts.push(f.model)
  if (f.audioCues ?? hasAudio) parts.push('audio cues')
  return parts.join(' · ')
}

// A monthly cap of 0 (or less) means there is none.
export function spendText(spent: number, monthlyCap: number): string {
  return monthlyCap > 0
    ? `Spent this month: $${spent.toFixed(2)} of $${monthlyCap.toFixed(2)}.`
    : `Spent this month: $${spent.toFixed(2)} (no monthly cap).`
}

export function fixFormSummary(f: FixForm, defaultEngine: string): string {
  const parts = [engineName(f.engine, defaultEngine)]
  if (f.model) parts.push(f.model)
  parts.push(f.cap.trim() ? `cap $${f.cap.trim()}` : 'no cost cap')
  return parts.join(' · ')
}

// ---- findings that point at a line ----

export interface Finding {
  key: string
  lineId: number | null // null: the line is gone, nothing to open
  where: string // "#12" or "After #12"
  text: string
}

// Long lists (every untranslated line) are cut; the rest is counted.
export const FINDINGS_SHOWN = 20

const where = (idx: number | null | undefined, prefix = '') =>
  typeof idx === 'number' ? `${prefix}#${lineNumber(idx)}` : '?'

export function coverageGroups(c: Coverage): { title: string; hint: string; items: Finding[] }[] {
  const entry = (group: string, e: CoverageEntry, i: number, text: string): Finding => ({
    key: `${group}-${e.id ?? e.after_id ?? 'x'}-${i}`,
    lineId: e.id ?? null,
    where: where(e.idx),
    text,
  })
  const groups = [
    {
      title: 'Long lines',
      hint: 'Much longer than its text: maybe several lines merged into one.',
      items: c.long_lines.map((e, i) =>
        entry('long', e, i, e.duration != null ? `${e.duration.toFixed(1)}s for ${e.char_count ?? 0} characters` : 'long line'),
      ),
    },
    {
      title: 'Large gaps',
      hint: 'A long silence between lines: maybe speech that was missed.',
      items: c.large_gaps.map((e, i) => ({
        key: `gap-${e.after_id ?? 'x'}-${i}`,
        lineId: e.after_id ?? null,
        where: where(e.after_idx, 'After '),
        text: e.gap_seconds != null ? `${e.gap_seconds.toFixed(1)}s silent` : 'gap',
      })),
    },
    { title: 'No source text', hint: 'The line has no source text.', items: c.blank_zh.map((e, i) => entry('zh', e, i, 'blank source')) },
    { title: 'Not translated', hint: 'The line has source text but no translation.', items: c.blank_en.map((e, i) => entry('en', e, i, e.zh ?? '')) },
  ]
  return groups.filter((g) => g.items.length > 0)
}

const PACING_ISSUES: Record<string, string> = {
  too_long_for_slot: 'Too long for its time slot',
  very_short_relative_to_slot: 'Very short for its time slot',
}

export function pacingFindings(flags: PacingFlag[]): Finding[] {
  return flags.map((f, i) => ({
    key: `pace-${f.id ?? 'x'}-${i}`,
    lineId: f.id,
    where: where(f.idx),
    text: [PACING_ISSUES[f.issue] ?? f.issue.replace(/_/g, ' '), f.detail].filter(Boolean).join(': '),
  }))
}

/**
 * Lines that use a consistency issue's term or one of its variants, from
 * one search per word. Results are merged by line id, in line order.
 */
export function mergeSearchHits(results: ReviewLine[][]): ReviewLine[] {
  const byId = new Map<number, ReviewLine>()
  for (const r of results) for (const l of r) if (!byId.has(l.id)) byId.set(l.id, l)
  return [...byId.values()].sort((a, b) => a.idx - b.idx)
}

// ---- emotion ----

export function emotionCounts(s: EmotionSummary): string {
  return Object.entries(s.by_emotion)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([e, n]) => `${e} ${n}`)
    .join(' · ')
}

// Strong tags first (they most need a careful translation), then line order.
export function emotionLines(s: EmotionSummary): EmotionSummary['lines'] {
  return [...s.lines].sort((a, b) => (b.intensity ?? 0) - (a.intensity ?? 0) || a.line_idx - b.line_idx)
}

// ---- provenance (variable-shape rows from debug_view.explain_line) ----

function field(o: unknown, key: string): string {
  if (o && typeof o === 'object' && key in o) {
    const v = (o as Record<string, unknown>)[key]
    if (typeof v === 'string' || typeof v === 'number') return String(v)
  }
  return ''
}

export function emotionText(e: unknown): string {
  const name = field(e, 'emotion')
  if (!name) return ''
  const n = field(e, 'intensity')
  return n ? `${name} (${n})` : name
}

export function glossaryText(rows: unknown[]): string[] {
  return rows
    .map((r) => {
      const a = field(r, 'term_original')
      const b = field(r, 'term_translation')
      return a ? (b ? `${a} → ${b}` : a) : ''
    })
    .filter(Boolean)
}

export function termsText(rows: unknown[]): string[] {
  return rows.map((r) => field(r, 'term')).filter(Boolean)
}

// ---- opening a line in the editor ----

// By permanent id wherever the route gives one. Emotion tags carry only the
// line's current position (no id in the route), so they open by number.
export type LineTarget = { lineId: number } | { lineNumber: number }
// Resolves to null once the line is open, else a plain message saying why not.
export type GoToLine = (t: LineTarget) => Promise<string | null>
