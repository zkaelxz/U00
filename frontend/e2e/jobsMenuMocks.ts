import { type Page } from '@playwright/test'

const now = () => Math.floor(Date.now() / 1000)

export function sampleJobs() {
  const t = now()
  const base = { progress: null, message: '', error: null, gpu_touching: false, updated_at: t, owned_by_me: true }
  return [
    { ...base, job_id: 'f1', status: 'done', description: 'Translate finished episode', started_at: t - 400, finished_at: t - 300 },
    { ...base, job_id: 'r1', status: 'running', description: 'Transcribe Episode 12 with a rather long title', started_at: t - 65, finished_at: null },
    { ...base, job_id: 'q1', status: 'queued', description: 'Dub Episode 3', started_at: null, finished_at: null },
    { ...base, job_id: 'e1', status: 'error', description: 'Export broken', error: 'Disk full', started_at: t - 900, finished_at: t - 800 },
  ]
}

// Jobs tied to titles and kinds, for the Jobs page (links, kind filter, Mine).
export function pageJobs() {
  const t = now()
  const base = { progress: null, message: '', error: null, gpu_touching: false, updated_at: t, owned_by_me: true, drama_id: null, kind: 'other', page: null }
  return [
    { ...base, job_id: 'translate_3', status: 'running', progress: 0.4, message: 'Batch 2 of 5', description: 'Translate Signal', started_at: t - 185, finished_at: null, drama_id: 3, kind: 'translate' },
    { ...base, job_id: 'dub_4', status: 'queued', description: 'Dub Kae', started_at: null, finished_at: null, drama_id: 4, kind: 'dub', owned_by_me: false },
    { ...base, job_id: 'transcribe_3', status: 'done', description: 'Transcribe Signal', started_at: t - 4000, finished_at: t - 3900, drama_id: 3, kind: 'transcribe', outcome: 'ok', outcome_message: '120 lines' },
    { ...base, job_id: 'burned_video_4', status: 'error', description: 'Export Kae', error: 'Disk full', started_at: t - 9000, finished_at: t - 8990, drama_id: 4, kind: 'export', outcome: 'failed' },
    { ...base, job_id: 'lncrawl_9', status: 'done', description: 'Import novel', started_at: t - 20 * 86_400, finished_at: t - 20 * 86_400 + 30, kind: 'import', outcome: 'ok' },
  ]
}

export const DRAMA_TITLES = [
  { id: 3, title_zh: '信号', title_en: 'Signal' },
  { id: 4, title_zh: null, title_en: 'Kae' },
]

export interface JobsMock {
  cancelled: string[]
  forceStopped: string[]
  deleted: string[]
  cleared: number
  stages: string[]
}

// Mocks the job list, Cancel, Delete, "Delete all finished" and a job's stage
// timing. Cancel and delete change the list the next GET returns.
// `titles` also mocks the library list the Jobs page reads for title names.
export async function mockJobsApi(page: Page, initial = sampleJobs() as Record<string, unknown>[], titles = false): Promise<JobsMock> {
  let jobs = initial
  const log: JobsMock = { cancelled: [], forceStopped: [], deleted: [], cleared: 0, stages: [] }
  const isFinished = (j: Record<string, unknown>) => ['done', 'error', 'cancelled'].includes(String(j.status))
  if (titles) {
    await page.route('**/api/library/dramas', (route) =>
      route.fulfill({ json: { count: DRAMA_TITLES.length, items: DRAMA_TITLES } }))
  }
  await page.route('**/api/jobs**', (route) => {
    const r = route.request()
    const path = new URL(r.url()).pathname
    const json = (body: unknown) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
    if (r.method() === 'POST' && path.endsWith('/cancel')) {
      const id = path.split('/')[3]
      log.cancelled.push(id)
      jobs = jobs.map((j) => (j.job_id === id ? { ...j, status: 'cancelled', finished_at: now() } : j))
      return json({ job_id: id, cancelled: true })
    }
    if (r.method() === 'POST' && path.endsWith('/force-stop')) {
      const id = path.split('/')[3]
      log.forceStopped.push(id)
      jobs = jobs.map((j) => (j.job_id === id ? { ...j, status: 'cancelled', can_force_stop: false, finished_at: now() } : j))
      return json({ job_id: id, force_stopped: true, status: 'cancelled', worker_still_running: true })
    }
    if (r.method() === 'POST' && path.endsWith('/delete')) {
      const id = path.split('/')[3]
      log.deleted.push(id)
      jobs = jobs.filter((j) => j.job_id !== id)
      return json({ job_id: id, deleted: true })
    }
    if (r.method() === 'POST' && path === '/api/jobs/clear-finished') {
      const n = jobs.filter(isFinished).length
      log.cleared += 1
      jobs = jobs.filter((j) => !isFinished(j))
      return json({ deleted_count: n })
    }
    if (path.endsWith('/stages')) {
      const id = path.split('/')[3]
      log.stages.push(id)
      return json({ job_id: id, runs: [{ run_started_at: 100, running: false, total_seconds: 90, cost_usd: 0.03, stages: [
        { stage: 'Preparing', started_at: 100, duration_seconds: 3.4, cost_usd: 0 },
        { stage: 'Translating', started_at: 103.4, duration_seconds: 86.6, cost_usd: 0.03 },
      ] }] })
    }
    if (path === '/api/jobs') return json({ items: jobs, count: jobs.length })
    return route.fallback()
  })
  return log
}

// Mocks the job list and Cancel; returns the cancelled ids.
export async function mockJobs(page: Page, jobs = sampleJobs()): Promise<string[]> {
  return (await mockJobsApi(page, jobs)).cancelled
}
