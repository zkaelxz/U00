import type {
  GlossaryAffectedLine,
  GlossaryAffectedParams,
  GlossaryAffectedPreview,
  GlossaryAffectedRunBody,
  TranslateRunEstimate,
} from '../../../types/translateStage'
import { buildEstimateParams, buildRunBody, type RunForm } from '../translateForm'

/** Ticked by default: every line not marked hand-edited. */
export function defaultSelection(lines: GlossaryAffectedLine[]): Set<number> {
  return new Set(lines.filter((l) => !l.hand_edited).map((l) => l.id))
}

/** The ids to send, in line order: hand-edited ones only when included. */
export function chosenIds(lines: GlossaryAffectedLine[], selected: Set<number>, includeHandEdited: boolean): number[] {
  return lines.filter((l) => selected.has(l.id) && (includeHandEdited || !l.hand_edited)).map((l) => l.id)
}

/** Preview query from the Translate form (engine, model, Reflect, cap); null while the cap is invalid. */
export function affectedParams(f: RunForm, termIds: number[]): GlossaryAffectedParams | null {
  const est = buildEstimateParams(f)
  if (!est) return null
  return {
    ...(termIds.length ? { term_ids: termIds } : {}),
    ...(est.engine ? { engine: est.engine } : {}),
    ...(est.model ? { model: est.model } : {}),
    ...(est.reflect ? { reflect: true } : {}),
    ...(est.job_cost_cap_usd !== undefined ? { job_cost_cap_usd: est.job_cost_cap_usd } : {}),
  }
}

/** The run body: the Translate form's settings (never Bulk: a batch can't take a line list). */
export function buildAffectedRunBody(
  f: RunForm,
  preview: GlossaryAffectedPreview,
  ids: number[],
  includeHandEdited: boolean,
  termIds: number[],
): GlossaryAffectedRunBody {
  const { force_retranslate: _force, bulk: _bulk, ...settings } = buildRunBody(f)
  return {
    ...settings,
    line_ids: ids,
    preview_hash: preview.preview_hash,
    include_hand_edited: includeHandEdited,
    ...(termIds.length ? { term_ids: termIds } : {}),
  }
}

/** The estimate that covers the chosen lines, and whether it is exact or an upper bound. */
export function selectionEstimate(
  preview: GlossaryAffectedPreview,
  ids: number[],
  includeHandEdited: boolean,
): { estimate: TranslateRunEstimate; exact: boolean } {
  const machine = preview.lines.filter((l) => !l.hand_edited).length
  if (!includeHandEdited) return { estimate: preview.estimate, exact: ids.length === machine }
  return { estimate: preview.estimate_with_hand_edited, exact: ids.length === preview.lines.length }
}

export function costText(e: TranslateRunEstimate, exact: boolean): string {
  if (e.free) return 'free'
  if (e.estimated_usd === null) return 'no cost'
  return `${exact ? 'about' : 'at most'} $${e.estimated_usd.toFixed(2)}`
}
