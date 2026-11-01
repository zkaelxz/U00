// The one-step novel start: import the link, make lines, find glossary terms, then stop for the
// person to review. Every step is chosen from what the server holds, never from a client flag, so
// a closed tab or a reload resumes where the title actually is.

export type ChainStep = 'import' | 'attach' | 'tag' | 'extract' | 'wait' | 'review'

export interface ChainState {
  chapters: number
  hasNovelText: boolean
  lines: number
  /** Status of the glossary-from-novel run the server holds: idle, queued, running, done, error, cancelled. */
  glossary: string
}

export function nextStep(s: ChainState): ChainStep {
  if (s.chapters === 0) return 'import'
  if (s.lines === 0) {
    if (!s.hasNovelText) return 'attach'
    return 'tag'
  }
  if (s.glossary === 'queued' || s.glossary === 'running') return 'wait'
  if (s.glossary === 'done') return 'review'
  return 'extract'
}

/** What the chain can do, so tests can stand in for the API. Each action resolves when its job has finished. */
export interface ChainActions {
  read: () => Promise<ChainState>
  importLink: () => Promise<void>
  attach: () => Promise<void>
  tag: () => Promise<void>
  extract: () => Promise<void>
  waitExtract: () => Promise<void>
}

export class ChainStall extends Error {}

const ACTION = {
  import: 'importLink', attach: 'attach', tag: 'tag', extract: 'extract', wait: 'waitExtract',
} as const

/**
 * Runs the steps in order until the glossary review is due. A step that leaves the title where it
 * was is a failure, not a retry, so a job that "succeeds" without writing anything cannot loop.
 */
export async function runChain(
  actions: ChainActions,
  onStep: (step: ChainStep) => void,
): Promise<ChainStep> {
  let previous: ChainStep | null = null
  for (let i = 0; i < 10; i++) {
    const step = nextStep(await actions.read())
    onStep(step)
    if (step === 'review') return step
    if (step === previous && step !== 'wait') throw new ChainStall(step)
    previous = step
    await actions[ACTION[step]]()
  }
  throw new ChainStall(previous ?? 'import')
}
