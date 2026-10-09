import type { Page, Request } from '@playwright/test'

// Mocks for Diagnostics > Setup "Install browser support" (a polled server job).

export const status = (o: Record<string, unknown> = {}) => ({
  playwright_installed: true, app_browser_installed: false, system_browser_found: false, free_mb: 5000,
  required_mb: 300, refusal: null, job_id: 'browser_install', job: null, last_result: null, ...o,
})
const running = (progress: number) => ({ status: 'running', progress, message: 'Downloading the browser…', error: null })

export async function mockBrowser(page: Page, sent: Request[], initial = status()) {
  let step = -1
  let cancelled = false
  await page.route((u) => u.pathname === '/api/diagnostics/browser', (r) => {
    if (step >= 0) step += 1
    const json = cancelled ? status({ last_result: { ok: false, message: 'Cancelled.', output_tail: [] },
      job: { status: 'cancelled', progress: 0.4, message: '', error: null } })
      : step < 0 ? initial
        : step <= 2 ? status({ job: running(0.4) })
          : status({
            app_browser_installed: true, refusal: 'Browser support is already installed.',
            job: { status: 'done', progress: 1, message: '', error: null },
            last_result: { ok: true, message: 'Browser support installed.', output_tail: ['done'] },
          })
    return r.fulfill({ json })
  })
  await page.route((u) => u.pathname === '/api/diagnostics/browser/install', (r) => {
    sent.push(r.request())
    step = 0
    return r.fulfill({ json: { job_id: 'browser_install', started: true } })
  })
  await page.route((u) => u.pathname === '/api/jobs/browser_install/cancel', (r) => {
    sent.push(r.request())
    cancelled = true
    return r.fulfill({ json: { job_id: 'browser_install', cancelled: true } })
  })
}
