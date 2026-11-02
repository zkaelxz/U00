// The one-step start for an audio or video title: transcribe, find glossary names, then stop for the person to review them.
// Every step is chosen from what the server holds, never from a client flag, so a closed tab or a reload resumes where the title is.

export type TitleFlowStep = 'transcribe' | 'extract' | 'wait' | 'review'

export interface TitleFlowState {
  lines: number
  /** Status of the glossary-from-lines run the server holds: idle, queued, running, done, error, cancelled. */
  glossary: string
}

export function nextTitleFlowStep(s: TitleFlowState): TitleFlowStep {
  if (s.lines === 0) return 'transcribe'
  if (s.glossary === 'queued' || s.glossary === 'running') return 'wait'
  if (s.glossary === 'done') return 'review'
  return 'extract'
}

export interface TitleFlowActions {
  read: () => Promise<TitleFlowState>
  transcribe: () => Promise<void>
  extract: () => Promise<void>
  waitExtract: () => Promise<void>
}

export class TitleFlowStall extends Error {}

const ACTION = { transcribe: 'transcribe', extract: 'extract', wait: 'waitExtract' } as const

/** Runs the steps until the glossary review is due. A step that leaves the title where it was is a failure, so a job that "succeeds" without writing anything cannot loop. */
export async function runTitleFlow(actions: TitleFlowActions, onStep: (step: TitleFlowStep) => void): Promise<TitleFlowStep> {
  let previous: TitleFlowStep | null = null
  for (let i = 0; i < 6; i++) {
    const step = nextTitleFlowStep(await actions.read())
    onStep(step)
    if (step === 'review') return step
    if (step === previous && step !== 'wait') throw new TitleFlowStall(step)
    previous = step
    await actions[ACTION[step]]()
  }
  throw new TitleFlowStall(previous ?? 'transcribe')
}
