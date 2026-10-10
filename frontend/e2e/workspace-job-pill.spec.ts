import { expect, test } from './fixtures'
import { serveJobs } from './workspaceJobPillMocks'

// Drama 3 is the seeded "Signal". Jobs are served from a mutable list so a
// spec can move a job from running to done between polls.
const now = () => Math.floor(Date.now() / 1000)
const rec = (over: Record<string, unknown>) => ({
  job_id: 'translate_3', status: 'running', progress: 0.42, message: 'Translating', error: null, description: 'Translate Signal',
  gpu_touching: false, started_at: now() - 30, finished_at: null, updated_at: now(), owned_by_me: true,
  drama_id: 3, kind: 'translate', ...over,
})

test('pill shows this title’s running job on every stage, not another title’s', async ({ page }) => {
  const jobs = { current: [rec({}), rec({ job_id: 'translate_1', drama_id: 1, progress: 0.9 })] }
  await serveJobs(page, jobs)
  await page.goto('/#/drama/3/source')
  const pill = page.getByTestId('job-pill')
  await expect(pill).toHaveText('● Translating 42%')
  for (const stage of ['Review', 'Export']) {
    await page.getByRole('navigation', { name: 'Stages' }).getByRole('link', { name: stage }).click()
    await expect(pill).toBeVisible()
  }
  await page.goto('/#/drama/2/source')
  await expect(page.getByTestId('drama-title')).toBeVisible()
  await expect(page.getByTestId('job-pill')).toHaveCount(0)
})

test('pill updates as progress changes and shows no percent when unknown', async ({ page }) => {
  const jobs = { current: [rec({ progress: null, kind: 'transcribe' })] }
  await serveJobs(page, jobs)
  await page.goto('/#/drama/3/source')
  await expect(page.getByTestId('job-pill')).toHaveText('● Transcribing')
  jobs.current = [rec({ progress: 0.7, kind: 'transcribe' })]
  await expect(page.getByTestId('job-pill')).toHaveText('● Transcribing 70%', { timeout: 8000 })
})

test('several jobs read "2 jobs"', async ({ page }) => {
  const jobs = { current: [rec({}), rec({ job_id: 'dub_3', kind: 'dub', status: 'queued', progress: null })] }
  await serveJobs(page, jobs)
  await page.goto('/#/drama/3/source')
  await expect(page.getByTestId('job-pill')).toHaveText('● 2 jobs')
})

test('popover shows the job and cancels it', async ({ page }) => {
  const jobs = { current: [rec({})] }
  const cancelled = await serveJobs(page, jobs)
  await page.goto('/#/drama/3/review')
  await page.getByTestId('job-pill').click()
  const region = page.getByRole('region', { name: 'Running on this title' })
  await expect(region.getByTestId('job-percent')).toHaveText('42%')
  await region.getByRole('button', { name: /^Cancel / }).click()
  await expect.poll(() => cancelled).toEqual(['translate_3'])
  await expect(page.getByTestId('job-pill')).toHaveText('Failed')
})

test('no Cancel for a job a remote admin does not own', async ({ page }) => {
  await page.route('**/api/auth/me', (route) =>
    route.fulfill({
      json: {
        auth_enabled: true, signed_in: true, sign_in_configured: true, zone: 'internet', permissions: ['library.read'],
        user: { id: 2, email: 'a@example.com', display_name: 'Admin', is_admin: true, is_local_owner: false },
      },
    }))
  await serveJobs(page, { current: [rec({ owned_by_me: false })] })
  await page.goto('/#/drama/3/source')
  await page.getByTestId('job-pill').click()
  const region = page.getByRole('region', { name: 'Running on this title' })
  await expect(region.getByText('Changing other people’s items is done on the main PC.')).toBeVisible()
  await expect(region.getByRole('button', { name: /^Cancel / })).toHaveCount(0)
})

test('flashes Done for 5 s when the job finishes, then disappears and refetches', async ({ page }) => {
  const jobs = { current: [rec({})] }
  await serveJobs(page, jobs)
  let dramaReads = 0
  page.on('request', (r) => { if (new URL(r.url()).pathname === '/api/library/dramas/3') dramaReads += 1 })
  await page.goto('/#/drama/3/source')
  await expect(page.getByTestId('job-pill')).toHaveText('● Translating 42%')
  const before = dramaReads
  jobs.current = [rec({ status: 'done', progress: 1, outcome: 'ok', finished_at: now() })]
  await expect(page.getByTestId('job-pill')).toHaveText('✓ Done', { timeout: 8000 })
  await expect.poll(() => dramaReads).toBeGreaterThan(before)
  await expect(page.getByTestId('job-pill')).toHaveCount(0, { timeout: 8000 })
})

test('flashes Failed when the job errors', async ({ page }) => {
  const jobs = { current: [rec({})] }
  await serveJobs(page, jobs)
  await page.goto('/#/drama/3/source')
  await expect(page.getByTestId('job-pill')).toBeVisible()
  jobs.current = [rec({ status: 'error', error: 'boom', finished_at: now() })]
  await expect(page.getByTestId('job-pill')).toHaveText('Failed', { timeout: 8000 })
})
