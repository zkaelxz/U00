/*
 * The steps an audio or video title goes through, with where it stands in each, for the Simple page's checklist.
 * The first step not yet done is the current one; Check has no signal of its own, so it is done once the title is exported.
 */
export type StepKey = 'add' | 'transcribe' | 'translate' | 'check' | 'export'
export type StepState = 'done' | 'current' | 'todo'

export interface StepInput {
  audioReady: boolean
  /** Null until the progress answer arrives. */
  lineCount: number | null
  translatedCount: number
  exported: boolean
  /** Lines the check queue lists (the Review risk view); null until known or when it can't be read. */
  riskCount?: number | null
}

export interface Step {
  key: StepKey
  title: string
  note: string
  state: StepState
}

export function titleSteps(i: StepInput): Step[] {
  const known = i.lineCount !== null
  const lines = i.lineCount ?? 0
  const allTranslated = lines > 0 && i.translatedCount >= lines
  const left = lines - i.translatedCount
  const rows: { key: StepKey; title: string; note: string; done: boolean }[] = [
    { key: 'add', title: 'Add', done: i.audioReady || lines > 0, note: i.audioReady ? 'Media added' : lines > 0 ? 'Lines added' : 'Add audio, video or a transcript' },
    { key: 'transcribe', title: 'Transcribe', done: lines > 0, note: lines > 0 ? `${lines} line${lines === 1 ? '' : 's'}` : 'Turn the speech into lines' },
    {
      key: 'translate', title: 'Translate', done: allTranslated,
      note: lines === 0 ? 'Needs lines first' : allTranslated ? 'Every line has English' : `${left} of ${lines} line${lines === 1 ? '' : 's'} left`,
    },
    { key: 'check', title: 'Check', done: i.exported, note: checkNote(i.riskCount ?? null) },
    { key: 'export', title: 'Export', done: i.exported, note: 'SRT, VTT, ASS or video' },
  ]
  if (!known) return rows.map((r) => ({ key: r.key, title: r.title, note: '', state: 'todo' as const }))
  const current = rows.findIndex((r) => !r.done)
  return rows.map((r, n) => ({ key: r.key, title: r.title, note: r.note, state: r.done ? 'done' : n === current ? 'current' : 'todo' }))
}

function checkNote(risk: number | null): string {
  if (risk === null) return 'Read and fix the lines'
  if (risk === 0) return 'Nothing needs a look'
  return risk === 1 ? '1 line needs a look' : `${risk} lines need a look`
}

/** The Check step's main button, which names the lines it opens; null when nothing needs a look and Export comes next. */
export function checkActionLabel(risk: number | null): string | null {
  if (risk === 0) return null
  if (risk === null) return 'Check the lines'
  return risk === 1 ? 'Check the line' : `Check the ${risk} lines`
}

/** "Step 4 of 5 · Check": where `key` sits among the steps, for the one-line fold of the step tiles. */
export function stepPlace(steps: Step[], key: StepKey): string | null {
  const at = steps.findIndex((s) => s.key === key)
  return at === -1 ? null : `Step ${at + 1} of ${steps.length} · ${steps[at].title}`
}
