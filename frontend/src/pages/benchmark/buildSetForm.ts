/*
 * Pure helpers for "Build a set from a reviewed title" (BuildSetSection.tsx): the form -> request,
 * the reasons it can't be sent yet, and the plain-words outcome. No React, so they are tested without
 * a browser (buildSetForm.test.ts).
 */
import type { BuildInclude, BuildSetRequest, BuildSetResult } from '../../api/benchmark'

export const DEFAULT_LINES_PER_CASE = 4
export const MAX_SET_NAME = 60

export interface BuildForm {
  dramaId: number | null
  setName: string
  include: BuildInclude
  lineStart: string
  lineEnd: string
  sceneCount: string
  linesPerCase: string
}

export const emptyBuildForm = (): BuildForm => ({
  dramaId: null, setName: '', include: 'reviewed', lineStart: '', lineEnd: '', sceneCount: '', linesPerCase: String(DEFAULT_LINES_PER_CASE),
})

/** A whole number >= 1 from a field, or null when blank or not one. */
export function wholeNumber(text: string): number | null {
  const t = text.trim()
  return /^\d+$/.test(t) && Number(t) >= 1 ? Number(t) : null
}

/** Reasons the form can't be sent (empty = it can). */
export function buildProblems(f: BuildForm): string[] {
  const out: string[] = []
  if (f.dramaId == null) out.push('Pick a title.')
  if (!f.setName.trim()) out.push('Give the set a name.')
  for (const [text, what] of [[f.lineStart, 'First line'], [f.lineEnd, 'Last line'], [f.sceneCount, 'Scenes']] as const) {
    if (text.trim() && wholeNumber(text) == null) out.push(`${what} must be a whole number, 1 or more.`)
  }
  const a = wholeNumber(f.lineStart)
  const b = wholeNumber(f.lineEnd)
  if (a != null && b != null && b < a) out.push('The last line comes before the first.')
  const per = wholeNumber(f.linesPerCase)
  if (per == null || per > 10) out.push('Lines per case must be 1 to 10.')
  return out
}

/** The request body; blank range and scene fields are left out. */
export function buildRequest(f: BuildForm, dryRun: boolean): BuildSetRequest {
  const body: BuildSetRequest = {
    drama_id: f.dramaId ?? 0,
    set_name: f.setName.trim(),
    include: f.include,
    lines_per_case: wholeNumber(f.linesPerCase) ?? DEFAULT_LINES_PER_CASE,
    dry_run: dryRun,
  }
  const start = wholeNumber(f.lineStart)
  const end = wholeNumber(f.lineEnd)
  const scenes = wholeNumber(f.sceneCount)
  if (start != null) body.line_start = start
  if (end != null) body.line_end = end
  if (scenes != null) body.scene_count = scenes
  return body
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`

/** What a preview or a build did, in plain words. */
export function describeBuild(r: BuildSetResult): string {
  const source = r.include === 'reviewed'
    ? `${plural(r.reviewed_lines, 'reviewed line')} of ${plural(r.lines_in_range, 'line')} in range`
    : `${plural(r.lines_in_range, 'line')} in range, reviewed or not`
  const cases = `${plural(r.case_count, 'case')} from ${plural(r.lines_used, 'line')}`
  if (r.dry_run) return `${source}. This would make ${cases}.`
  const skipped = r.skipped ? `; ${plural(r.skipped, 'case')} already in the set ${r.skipped === 1 ? 'was' : 'were'} skipped` : ''
  return `Added ${plural(r.added, 'case')} to “${r.set_name}”${skipped}. (${source}.)`
}
