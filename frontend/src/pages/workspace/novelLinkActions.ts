import { getNovelGlossary } from '../../api/autotuneGlossary'
import { narrationApi } from '../../api/dub'
import { probeJob } from '../../api/jobs'
import { getRawChapters } from '../../api/novelChapters'
import { sourceImportJobId } from '../../api/sourcesImport'
import { startNovelUrlImport } from '../../api/sourcesExtraction'
import { attachNovelFromSources, getNovelStatus, getWorkflowProgress } from '../../api/workspace'
import { isActiveStatus, isTerminalStatus } from '../../types/jobs'
import { startExtraction } from './stages/useGlossaryRun'
import type { ChainActions } from './novelLinkChain'

const POLL_MS = 2000
// A job id answers 404 for a moment after its start in some paths; give it a few reads before calling it lost.
const MISSING_READS = 5

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

export class StepFailed extends Error {}

async function waitForJob(jobId: string): Promise<void> {
  for (let missing = 0; missing < MISSING_READS; ) {
    const job = await probeJob(jobId)
    if (!job) {
      missing += 1
    } else if (isTerminalStatus(job.status)) {
      if (job.status === 'done') return
      throw new StepFailed(job.error || job.message || job.status)
    } else {
      missing = 0
    }
    await sleep(POLL_MS)
  }
  throw new StepFailed('The job could not be found.')
}

async function waitForExtraction(id: number): Promise<void> {
  for (;;) {
    const s = await getNovelGlossary(id)
    if (!isActiveStatus(s.status)) {
      if (s.status === 'error' || s.status === 'cancelled') throw new StepFailed(s.message || s.status)
      return
    }
    await sleep(POLL_MS)
  }
}

/** The real calls behind the chain, for title `id`. Reads come from the server on every step. */
export function novelLinkActions(id: number, url: string): ChainActions {
  return {
    read: async () => {
      const [chapters, novel, progress, glossary] = await Promise.all([
        getRawChapters(id, 0, 1), getNovelStatus(id), getWorkflowProgress(id), getNovelGlossary(id),
      ])
      return {
        chapters: chapters.total,
        hasNovelText: novel.has_novel_text,
        lines: progress.line_count,
        glossary: glossary.status,
      }
    },
    importLink: async () => {
      const started = await startNovelUrlImport(url, id, {})
      await waitForJob(started.job_id || sourceImportJobId(id))
    },
    attach: async () => {
      await attachNovelFromSources(id, 'replace')
    },
    tag: async () => {
      const cfg = await narrationApi.config(id)
      const engine = cfg.engines.find((e) => e.key_configured)?.key ?? cfg.default_engine
      const started = await narrationApi.run(id, { engine })
      await waitForJob(started.job_id)
    },
    extract: async () => {
      const out = await startExtraction(id, 'novel')
      if (out.error) throw out.error
      if (out.problem) throw new StepFailed(out.problem)
      await waitForExtraction(id)
    },
    waitExtract: () => waitForExtraction(id),
  }
}
