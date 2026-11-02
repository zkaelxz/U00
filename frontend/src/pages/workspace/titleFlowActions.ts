// The real calls behind the audio and video one-step start, for title `id`.
import { getLinesGlossary } from '../../api/autotuneGlossary'
import { getWorkflowProgress, startTranscribe } from '../../api/workspace'
import { isActiveStatus } from '../../types/jobs'
import { startExtraction } from './stages/useGlossaryRun'
import { StepFailed, waitForJob } from './novelLinkActions'
import type { TitleFlowActions } from './titleFlowChain'

const POLL_MS = 2000
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

async function waitForExtraction(id: number): Promise<void> {
  for (;;) {
    const s = await getLinesGlossary(id)
    if (!isActiveStatus(s.status)) {
      if (s.status === 'error' || s.status === 'cancelled') throw new StepFailed(s.message || s.status)
      return
    }
    await sleep(POLL_MS)
  }
}

export function titleFlowActions(id: number): TitleFlowActions {
  return {
    read: async () => {
      const [progress, glossary] = await Promise.all([getWorkflowProgress(id), getLinesGlossary(id)])
      return { lines: progress.line_count, glossary: glossary.status }
    },
    // An empty request runs with the options saved on the title, the same as Transcribe's own Start.
    transcribe: async () => {
      const started = await startTranscribe(id, {})
      await waitForJob(started.job_id)
    },
    extract: async () => {
      const out = await startExtraction(id, 'lines')
      if (out.error) throw out.error
      if (out.problem) throw new StepFailed(out.problem)
      await waitForExtraction(id)
    },
    waitExtract: () => waitForExtraction(id),
  }
}
