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

// Mocks the job list and Cancel; returns the cancelled ids.
export async function mockJobs(page: Page, jobs = sampleJobs()): Promise<string[]> {
  const cancelled: string[] = []
  await page.route('**/api/jobs**', (route) => {
    const r = route.request()
    const path = new URL(r.url()).pathname
    const json = (body: unknown) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
    if (r.method() === 'POST' && path.endsWith('/cancel')) {
      cancelled.push(path.split('/')[3])
      return json({ job_id: path.split('/')[3], cancelled: true })
    }
    if (path === '/api/jobs') return json({ items: jobs })
    return route.fallback()
  })
  return cancelled
}
