import { ApiError } from '../../../../api/client'
import type { ResegmentPreview } from '../../../../types/restructure'
import type { TranslateEngine } from '../../../../types/translate'
import type { TranslateRunConfig } from '../../../../types/translateStage'
import { humanize } from '../../../../components/labels'
import { reflectAvailable } from '../../translateForm'
import { spendText } from './reviewResults'
import { JOB_RUNNING_MESSAGE } from './reviewStructure'

export function resegmentSummary(p: ResegmentPreview): string {
  const notes = `${p.notes} note${p.notes === 1 ? '' : 's'}`
  return `${p.line_count_before} → ${p.line_count_after} lines; ${p.changed.length} change; ${p.translated} translated, ${p.flagged} flagged, ${notes} would be split`
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
