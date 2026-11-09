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
    { key: 'check', title: 'Check', done: i.exported, note: 'Read and fix the lines' },
    { key: 'export', title: 'Export', done: i.exported, note: 'SRT, VTT, ASS or video' },
  ]
  if (!known) return rows.map((r) => ({ key: r.key, title: r.title, note: '', state: 'todo' as const }))
  const current = rows.findIndex((r) => !r.done)
  return rows.map((r, n) => ({ key: r.key, title: r.title, note: r.note, state: r.done ? 'done' : n === current ? 'current' : 'todo' }))
}
