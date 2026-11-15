import { applyNovelGlossary, getNovelGlossary } from '../../api/autotuneGlossary'
import { narrationApi } from '../../api/dub'
import { probeJob } from '../../api/jobs'
import { sourceImportJobId } from '../../api/sourcesImport'
import { startNovelUrlImport } from '../../api/sourcesExtraction'
import { getTranslateConfig, startTranslateRun } from '../../api/translateStage'
import { addChaptersFromSources } from '../../api/workspace'
import { isTerminalStatus } from '../../types/jobs'
import { defaultTermSelection } from './stages/autotuneGlossary'
import { applyRequest } from './stages/glossaryExtract'
import { applyStartToForm, buildRunBody, initialForm, loadPresetStart } from './translateForm'

const POLL_MS = 2000
// A job id answers 404 for a moment after its start in some paths; give it a few reads before calling it lost.
const MISSING_READS = 5

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

export class StepFailed extends Error {}

export async function waitForJob(jobId: string): Promise<void> {
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

/**
 * The opt-in shortcut past the glossary review: adds the proposed terms that are not in the glossary yet
 * (those with a suggested translation; the series glossary is where they go, so a title without a series
 * adds none) and starts the translation with the form's own defaults, the same body the Translate button sends.
 */
export async function translateWithoutReview(id: number, hasSeries: boolean): Promise<string> {
  if (hasSeries) {
    const status = await getNovelGlossary(id)
    const proposals = status.proposals ?? []
    const chosen = proposals
      .filter((p) => defaultTermSelection(proposals).has(p.term) && p.suggested_translation.trim())
      .map((p) => p.term)
    if (chosen.length) await applyNovelGlossary(id, applyRequest(chosen, status.run_id, undefined))
  }
  const config = await getTranslateConfig(id)
  const form = applyStartToForm(initialForm(config, loadPresetStart(id)), config.translate_start, config)
  return (await startTranslateRun(id, buildRunBody(form))).job_id
}

export type AddStep = 'import' | 'text' | 'lines'

/**
 * Adds chapters from a link to a title that is already being translated: imports them, appends only
 * the new text to the translation text (the server refuses when that text was edited), then cuts just
 * that text into lines after the saved ones, which are not touched. It starts no translation.
 * `chaptersBefore` is how many chapters were saved before the import; the server refuses if the
 * translation text does not hold exactly those. A retry after a failed lines step finishes the lines
 * the first attempt left. Resolves to the number of characters added or finished.
 */
export async function addChaptersFromLink(
  id: number, url: string, onStep: (s: AddStep) => void, chaptersBefore?: number,
): Promise<number> {
  onStep('import')
  const started = await startNovelUrlImport(url, id, {})
  await waitForJob(started.job_id || sourceImportJobId(id))
  onStep('text')
  const result = await addChaptersFromSources(id, chaptersBefore === undefined ? undefined : chaptersBefore + 1)
  // Text an earlier attempt added but did not get lines for is finished here too: the server holds how much.
  const waiting = result.pending_chars ?? 0
  if (waiting > 0) {
    onStep('lines')
    const cfg = await narrationApi.config(id)
    const engine = cfg.engines.find((e) => e.key_configured)?.key ?? cfg.default_engine
    await waitForJob((await narrationApi.run(id, { engine, append: true })).job_id)
  }
  return Math.max(result.added_chars, waiting)
}
