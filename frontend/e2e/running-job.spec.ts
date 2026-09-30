import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

// A job genuinely running in the seeded API process, against the real API
// (no page.route mocks): Diagnostics' Cancel ends it and the list shows it
// cancelled, and the Library refuses to delete its drama with a 409.
//
// The job comes from the e2e launcher's test-only hook (POST
// /api/e2e/jobs/hold, frontend/e2e/serve_seeded_api.py): a real
// background_jobs thread job that mirrors itself to job_records, heartbeats,
// and stops as "cancelled" once its cancel flag is set. Each test creates its
// own drama and removes its job and drama afterwards, so the seeded three and
// the empty job list other specs expect are unchanged.

const LOCAL = { 'X-Baihe-Local': '1' }

async function createDrama(request: APIRequestContext, title: string): Promise<number> {
  const r = await request.post('/api/dramas', { data: { source_language: 'zh', title_en: title }, headers: LOCAL })
  expect(r.ok()).toBeTruthy()
  return (await r.json()).id
}

async function holdJob(request: APIRequestContext, jobId: string, description: string) {
  const r = await request.post('/api/e2e/jobs/hold', { data: { job_id: jobId, description }, headers: LOCAL })
  expect(r.ok()).toBeTruthy()
  await expect.poll(async () => (await (await request.get(`/api/jobs/${jobId}`)).json()).status).toBe('running')
}

async function jobStatus(request: APIRequestContext, jobId: string): Promise<string | null> {
  const r = await request.get(`/api/jobs/${jobId}`)
  return r.ok() ? (await r.json()).status : null
}

// Cancels (if still running), waits for the job to stop, then drops its record
// and deletes the drama. Safe to call whatever state the test left behind.
async function cleanUp(request: APIRequestContext, jobId: string | null, dramaId: number | null) {
  if (jobId) {
    if ((await jobStatus(request, jobId)) === 'running') {
      await request.post(`/api/jobs/${jobId}/cancel`, { headers: LOCAL })
    }
    await expect.poll(() => jobStatus(request, jobId)).not.toBe('running')
    await request.post('/api/e2e/jobs/forget', { data: { job_id: jobId }, headers: LOCAL })
  }
  if (dramaId !== null) {
    await request.delete(`/api/dramas/${dramaId}?confirm=true&confirm_text=DELETE`, { headers: LOCAL })
  }
}

const jobRow = (page: Page, description: string) =>
  page.getByTestId('job-list').locator('tr', { hasText: description })

test.describe('a running job (real API)', () => {
  let jobId: string | null = null
  let dramaId: number | null = null

  test.afterEach(async ({ request }) => {
    await cleanUp(request, jobId, dramaId)
    jobId = null
    dramaId = null
  })

  test('Diagnostics: Cancel stops the running job and the list shows it cancelled, also after a reload', async ({ page, request }) => {
    dramaId = await createDrama(request, 'Running Job E2E Diagnostics')
    jobId = `notes_${dramaId}`
    const description = `E2E held job ${dramaId}`
    await holdJob(request, jobId, description)

    await page.goto('/#/diagnostics')
    const row = jobRow(page, description)
    await expect(row).toContainText('Running')

    const cancelled = page.waitForResponse((r) => r.url().endsWith(`/api/jobs/${jobId}/cancel`) && r.request().method() === 'POST')
    await row.getByRole('button', { name: `Cancel ${description}` }).click()
    expect((await cancelled).status()).toBe(200)

    // Cancellation is cooperative and asynchronous: the job's thread sees
    // the flag, stops, and its record turns "cancelled".
    await expect.poll(() => jobStatus(request, jobId as string)).toBe('cancelled')

    // Nothing is running now, so the list folds into the collapsed Jobs
    // section; after a reload the page reads the record fresh.
    await page.reload()
    const fold = page.locator('details.section', { has: page.locator('summary', { hasText: 'None running' }) })
    await expect(fold).toBeVisible()
    if (!(await fold.evaluate((d) => (d as HTMLDetailsElement).open))) await fold.locator('summary').click()
    const after = jobRow(page, description)
    await expect(after).toContainText('Cancelled')
    await expect(after.getByRole('button', { name: `Cancel ${description}` })).toHaveCount(0)
  })

  test('Library: deleting a drama with a running job is refused (409) and the drama stays listed', async ({ page, request }) => {
    const title = 'Running Job E2E Library'
    dramaId = await createDrama(request, title)
    jobId = `transcribe_${dramaId}`
    await holdJob(request, jobId, `E2E held job ${dramaId}`)

    await page.goto('/')
    await page.getByLabel('Search title or summary').fill('Running Job E2E')
    await expect(page.getByTestId('drama-count')).toHaveText('1 drama')
    await page.getByRole('region', { name: 'Dramas' }).getByRole('button', { name: `Details: ${title}` }).click()
    const detail = page.getByRole('dialog', { name: title })
    await expect(detail).toBeVisible()

    await detail.getByRole('button', { name: 'Delete drama…' }).click()
    await detail.getByLabel('Type DELETE to confirm').fill('DELETE')
    const refused = page.waitForResponse((r) => r.url().includes(`/api/dramas/${dramaId}`) && r.request().method() === 'DELETE')
    await detail.getByRole('button', { name: 'Delete permanently' }).click()
    expect((await refused).status()).toBe(409)
    await expect(detail.getByRole('alert')).toContainText('Not deleted: a background job is still running for this drama')

    // Still there: the dialog stays open, the drama is still in the list and the API.
    await expect(detail).toBeVisible()
    expect((await request.get(`/api/library/dramas/${dramaId}`)).status()).toBe(200)
    await page.reload()
    await page.getByLabel('Search title or summary').fill('Running Job E2E')
    await expect(page.getByTestId('drama-count')).toHaveText('1 drama')
    await expect(page.getByRole('region', { name: 'Dramas' }).getByRole('link', { name: title, exact: true })).toBeVisible()
    // The job was not touched by the refused delete.
    expect(await jobStatus(request, jobId)).toBe('running')
  })
})
