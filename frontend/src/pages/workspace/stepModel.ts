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
    { key: 'check', title: 'Check', done: i.exported, note: checkNote(i.riskCount ?? null, allTranslated) },
    { key: 'export', title: 'Export', done: i.exported, note: 'SRT, VTT, ASS or video' },
  ]
  if (!known) return rows.map((r) => ({ key: r.key, title: r.title, note: '', state: 'todo' as const }))
  const current = rows.findIndex((r) => !r.done)
  return rows.map((r, n) => ({ key: r.key, title: r.title, note: r.note, state: r.done ? 'done' : n === current ? 'current' : 'todo' }))
}

// A count of zero says nothing has been looked at until every line has English (with no lines, nothing could be).
function checkNote(risk: number | null, reached: boolean): string {
  if (risk === null || (risk === 0 && !reached)) return 'Read and fix the lines'
  if (risk === 0) return 'Nothing needs a look'
  return risk === 1 ? '1 line needs a look' : `${risk} lines need a look`
}

/** The Check step's main button, which names the lines it opens; null when nothing needs a look and Export comes next. */
export function checkActionLabel(risk: number | null): string | null {
  if (risk === 0) return null
  if (risk === null) return 'Check the lines'
  return risk === 1 ? 'Check the line' : `Check the ${risk} lines`
}

/** "Step 4 of 5 · Check": where `key` sits among the steps, for the phone's one button that opens the steps. */
export function stepPlace(steps: Step[], key: StepKey): string | null {
  const at = steps.findIndex((s) => s.key === key)
  return at === -1 ? null : `Step ${at + 1} of ${steps.length} · ${steps[at].title}`
}

/**
 * The step to mark: the one whose page is open, else the current one; once every step is done, the last.
 * Null while the progress is loading and no step's page is open, so nothing is marked that may be wrong.
 */
export function focusStep(steps: Step[], open: StepKey | null): StepKey | null {
  if (open) return open
  const current = steps.find((s) => s.state === 'current')
  if (current) return current.key
  return steps.length > 0 && steps.every((s) => s.state === 'done') ? steps[steps.length - 1].key : null
}

/**
 * On a phone, whether the main button sits in the panel the steps button opens rather than under the header: on the
 * Check step the check card comes first, so on Review, and on the title page while Check is the current step.
 */
export function mainInStepsPanel(openStep: StepKey | null, current: StepKey | null, titlePage: boolean): boolean {
  return openStep === 'check' || (titlePage && current === 'check')
}

/** Whether the title page opens the lines on All: on the Translate step some lines have no English, and the check queue never lists those. */
export const linesOpenOnAll = (titlePage: boolean, i: StepInput): boolean =>
  titlePage && titleSteps(i).find((s) => s.state === 'current')?.key === 'translate'

export type StepWord = 'Done' | 'Now' | 'Next'

export interface StepLink {
  key: StepKey
  state: StepState
  /** Null for the steps after Next, and for every step while the progress is loading. */
  word: StepWord | null
  name: string
  /** Read after a comma, so it is shorter than the step's tooltip. */
  note: string | null
  /** The longer guidance the step tiles used to show, kept as the link's tooltip so none of it is lost. */
  tip: string
  marked: boolean
}

/** The one-line step links: "Done · Transcribe, 24 lines", "Now · Check, 3 lines need a look", "Next · Export", "Export". */
export function stepLinks(i: StepInput, open: StepKey | null = null): StepLink[] {
  const steps = titleSteps(i)
  const marked = focusStep(steps, open)
  const at = steps.findIndex((s) => s.state === 'current')
  const lines = i.lineCount ?? 0
  const left = lines - i.translatedCount
  const risk = i.riskCount ?? null
  const shortNote = (s: Step): string | null => {
    if (i.lineCount === null) return null
    if (s.key === 'transcribe' && s.state === 'done') return `${lines} ${lines === 1 ? 'line' : 'lines'}`
    if (s.key === 'translate' && lines > 0 && left > 0) return `${left} of ${lines} left`
    if (s.key === 'check' && risk !== null && s.state !== 'done') {
      if (risk === 0) return s.state === 'current' ? 'nothing needs a look' : null
      return risk === 1 ? '1 line needs a look' : `${risk} lines need a look`
    }
    return null
  }
  return steps.map((s, n) => ({
    key: s.key,
    state: s.state,
    word: s.state === 'done' ? 'Done' : s.state === 'current' ? 'Now' : at !== -1 && n === at + 1 ? 'Next' : null,
    name: s.title,
    note: shortNote(s),
    tip: s.note,
    marked: s.key === marked,
  }))
}
