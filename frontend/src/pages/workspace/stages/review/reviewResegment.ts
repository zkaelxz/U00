import { ApiError } from '../../../../api/client'
import type { ResegmentPreview, ResplitResult, ResplitSensitivity } from '../../../../types/restructure'
import type { TranslateEngine } from '../../../../types/translate'
import type { TranslateRunConfig } from '../../../../types/translateStage'
import { humanize } from '../../../../components/labels'
import { reflectAvailable } from '../../translateForm'
import { spendText } from './reviewResults'


export const JOB_RUNNING_MESSAGE = 'A job is running on this drama. Structure edits wait until it finishes.'

export function resegmentSummary(p: ResegmentPreview): string {
  const notes = `${p.notes} note${p.notes === 1 ? '' : 's'}`
  return `${p.line_count_before} → ${p.line_count_after} lines; ${p.changed.length} change; ${p.translated} translated, ${p.flagged} flagged, ${notes} would be split`
}

/** "Split 31 lines into 118; speakers re-assigned" from a re-split summary. */
export function resplitSummary(r: ResplitResult): string {
  const n = r.split_lines ?? 0
  if (n === 0) return r.note || 'No line is over the length limits. Nothing changed.'
  const pieces = (r.line_count ?? 0) - (r.lines_before ?? 0) + n
  const parts = [`Split ${n} line${n === 1 ? '' : 's'} into ${pieces}`]
  if (r.timing === 'aligned') parts.push(`${r.aligned_lines ?? 0} timed from the audio`)
  if (r.speakers_reassigned) parts.push('speakers re-assigned')
  if (r.cleared_translations) parts.push(`${r.cleared_translations} translation${r.cleared_translations === 1 ? '' : 's'} cleared`)
  return parts.join('; ') + '.' + (r.note ? ` ${r.note}` : '')
}

export const RESPLIT_SENSITIVITIES: { value: ResplitSensitivity; label: string }[] = [
  { value: 'normal', label: 'Normal' },
  { value: 'more', label: 'More' },
  { value: 'sentence', label: 'Sentence by sentence' },
]

/** Seconds offered for "Also split by duration"; null keeps the preset's own limit. */
export const RESPLIT_DURATION_CAPS = [5, 10, 15, 20]

// The Re-split options' draft (hooks/useStageDraft, stage "review.resplit");
// capSec 0 means the preset's own duration limit.
export const RESPLIT_DRAFT_STAGE = 'review.resplit'
export const RESPLIT_DRAFT_SHAPE = { align: false, sensitivity: '', capSec: 0 }

/** "Preview: 31 lines would be split into 118." from a dry-run result. */
export function resplitPreviewSummary(r: ResplitResult): string {
  const n = r.split_lines ?? 0
  if (n === 0) return r.note || 'Preview: no line would be split.'
  const cleared = r.cleared_translations
    ? ` ${r.cleared_translations} translation${r.cleared_translations === 1 ? '' : 's'} would be cleared.`
    : ''
  return `Preview: ${n} line${n === 1 ? '' : 's'} would be split into ${r.pieces ?? 0}.${cleared}`
}

/** The server asks for confirm=true when a long line already has English. */
export function resplitNeedsConfirm(e: unknown): boolean {
  return e instanceof ApiError && e.status === 422 && /confirm/i.test(e.message)
}

// Translation-only engines cannot suggest split points (the server refuses
// them). The engine list carries no flag for this, so the Translate stage's
// own list of such engines is reused.
export function canResegmentWith(engine: string): boolean {
  return reflectAvailable(engine)
}

export function resegmentEngines(engines: TranslateEngine[]): TranslateEngine[] {
  return engines.filter((e) => canResegmentWith(e.name))
}

/** Before starting: what running the preview costs, with this month's spend when known. */
export function resegmentCostNote(
  config: Pick<TranslateRunConfig, 'engines' | 'month_spend' | 'monthly_cap_usd' | 'cap_applies_by_engine'> | null,
  engine: string,
): string {
  const info = config?.engines.find((e) => e.name === engine)
  if (info?.free) return `${info.label} is free to run.`
  if (!config) return 'A paid AI call, counted toward the monthly spending cap.'
  const spent = spendText(config.month_spend, config.monthly_cap_usd)
  return config.cap_applies_by_engine[engine] === false
    ? `An AI call; this engine is not counted toward the monthly cap. ${spent}`
    : `A paid AI call, counted toward the monthly spending cap. ${spent}`
}

// The preview carries no cost figure; the call is logged with the drama's usage.
export const RESEGMENT_COST_RECORDED = 'The AI cost is logged with this drama’s usage (Library → Cost by drama).'

const plural = (n: number, one: string) => `${n} ${one}${n === 1 ? '' : 's'}`

/** What applying drops on the lines being split. */
export function droppedText(p: Pick<ResegmentPreview, 'translated' | 'flagged' | 'notes'>): string {
  const parts = [
    p.translated ? plural(p.translated, 'translation') : '',
    p.flagged ? plural(p.flagged, 'flag') : '',
    p.notes ? plural(p.notes, 'note') : '',
  ].filter(Boolean)
  if (parts.length === 0) return 'Lines long enough to split carry translations, flags or notes; any line that is split loses them.'
  const list = parts.length === 1 ? parts[0] : `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}`
  return `${list} on the lines being split will be dropped.`
}

export function llmPreviewSummary(p: ResegmentPreview & { engine: string }): string {
  const n = p.changed.length
  return `${p.line_count_before} → ${p.line_count_after} lines · ${n} line${n === 1 ? '' : 's'} split · by ${humanize('engine', p.engine)}`
}

const RESEGMENT_CONFIRM_MESSAGE =
  'Lines being split now carry translations, flags or notes, which would be dropped. Type the word to apply anyway.'

export const RESEGMENT_PREVIEW_AGAIN = 'The lines changed since this preview. Preview again.'

const RESEGMENT_PREVIEW_GONE = 'This preview is no longer on the server. Preview again.'

type LlmApplyProblem = 'confirm' | 'changed' | 'gone' | 'job'

/**
 * Why applying the AI preview was refused, from the start request's error or
 * the apply job's error text; null = show the error as it is.
 */
export function llmApplyProblem(e: unknown): LlmApplyProblem | null {
  if (typeof e === 'string') {
    if (/confirm/i.test(e)) return 'confirm'
    if (/changed since the preview/i.test(e)) return 'changed'
    return null
  }
  if (!(e instanceof ApiError)) return null
  if (e.status === 422 && /confirm/i.test(e.message)) return 'confirm'
  if (e.status === 404) return 'gone'
  if (e.status === 409) return /job|already running/i.test(e.message) ? 'job' : 'changed'
  return null
}

export function llmApplyProblemText(p: LlmApplyProblem): string {
  if (p === 'confirm') return RESEGMENT_CONFIRM_MESSAGE
  if (p === 'gone') return RESEGMENT_PREVIEW_GONE
  if (p === 'job') return JOB_RUNNING_MESSAGE
  return RESEGMENT_PREVIEW_AGAIN
}
