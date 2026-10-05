import { type Page } from '@playwright/test'

const now = () => Math.floor(Date.now() / 1000)

// Serves GET /api/jobs from a mutable list so a spec can move a job between polls;
// Cancel marks the job cancelled and returns the cancelled ids.
export async function serveJobs(page: Page, jobs: { current: unknown[] }) {
  const cancelled: string[] = []
  await page.route('**/api/jobs**', (route) => {
    const r = route.request()
    const path = new URL(r.url()).pathname
    if (r.method() === 'POST' && path.endsWith('/cancel')) {
      const id = path.split('/')[3]
      cancelled.push(id)
      jobs.current = (jobs.current as { job_id: string }[]).map((j) => (j.job_id === id ? { ...j, status: 'cancelled', finished_at: now() } : j))
      return route.fulfill({ json: { job_id: id, cancel_requested: true, status: 'cancelled' } })
    }
    if (path === '/api/jobs') return route.fulfill({ json: { items: jobs.current } })
    return route.fallback()
  })
  return cancelled
}
