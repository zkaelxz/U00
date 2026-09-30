import { expect, test, type Page } from '@playwright/test'

// Step 115b: "Import with lightnovel-crawler" in Source > Novel text. The
// lncrawl endpoints and the job are mocked (real lncrawl never runs); the
// drama read hits the real seeded API.

async function mockStatus(page: Page, installed: boolean) {
  await page.route('**/api/novel/lncrawl', (route) =>
    route.fulfill({ json: { installed, path_configured: false } }),
  )
}

async function openNovel(page: Page) {
  await page.goto('/#/drama/1/source')
  await page.locator('.section-title', { hasText: /^Novel text$/ }).click()
}

test('hidden when lightnovel-crawler is not installed', async ({ page }) => {
  await mockStatus(page, false)
  await openNovel(page)
  await expect(page.getByRole('button', { name: 'Attach EPUB' })).toBeVisible()
  await expect(page.getByText('Import with lightnovel-crawler')).toHaveCount(0)
})

test('starts an import, shows progress with cancel, then the result', async ({ page }) => {
  await mockStatus(page, true)
  let body: unknown = null
  let polls = 0
  await page.route('**/api/novel/dramas/1/lncrawl', async (route) => {
    body = route.request().postDataJSON()
    await route.fulfill({ json: { job_id: 'lncrawl_1' } })
  })
  await page.route('**/api/jobs/lncrawl_1', (route) => {
    if (body === null) return route.fulfill({ status: 404, json: { code: 'not_found', message: 'x' } })
    polls += 1
    return route.fulfill({
      json: polls < 3
        ? { job_id: 'lncrawl_1', status: 'running', progress: 0.1, message: 'lightnovel-crawler is downloading... (0 min)', error: null, updated_at: 1 }
        : { job_id: 'lncrawl_1', status: 'done', progress: 1, message: '', error: null, updated_at: 2, result: { char_count: 4321, epub_chapters: 12 }, outcome: 'ok' },
    })
  })
  await openNovel(page)
  await page.locator('.section-title', { hasText: /^Import with lightnovel-crawler$/ }).click()
  await expect(page.getByText("Whether a site's terms allow downloading a title is your call")).toBeVisible()
  const start = page.getByRole('button', { name: 'Start import' })
  await expect(start).toBeDisabled()
  await page.getByLabel('Novel address', { exact: true }).fill('ftp://x.example/a')
  await expect(page.getByRole('alert').filter({ hasText: 'http://' })).toBeVisible()
  await expect(start).toBeDisabled()
  await page.getByLabel('Novel address', { exact: true }).fill('https://novels.example.com/book/1')
  await page.getByLabel('Chapters', { exact: true }).selectOption('first')
  await expect(start).toBeDisabled()
  await page.getByLabel('How many', { exact: true }).fill('10')
  await expect(start).toBeEnabled()
  await start.click()
  await expect(page.getByTestId('job-status')).toContainText('running')
  await expect(page.getByRole('button', { name: /cancel job/i })).toBeVisible()
  await expect(page.getByRole('status').filter({ hasText: 'Imported 4,321 characters (12 EPUB sections).' })).toBeVisible()
  expect(body).toEqual({ url: 'https://novels.example.com/book/1', chapters: 'first', count: 10, mode: 'replace' })
})

test('a failed import shows the short error and what lncrawl printed', async ({ page }) => {
  await mockStatus(page, true)
  let started = false
  await page.route('**/api/novel/dramas/1/lncrawl', async (route) => {
    started = true
    await route.fulfill({ json: { job_id: 'lncrawl_1' } })
  })
  await page.route('**/api/jobs/lncrawl_1', (route) =>
    started
      ? route.fulfill({ json: {
        job_id: 'lncrawl_1', status: 'error', progress: 0.1, message: '', updated_at: 2,
        error: 'RuntimeError: lightnovel-crawler stopped with exit code 1.',
        result: { failed_reason: 'lncrawl', detail: 'No crawler found for https://novels.example.com' },
      } })
      : route.fulfill({ status: 404, json: { code: 'not_found', message: 'x' } }),
  )
  await openNovel(page)
  await page.locator('.section-title', { hasText: /^Import with lightnovel-crawler$/ }).click()
  await page.getByLabel('Novel address', { exact: true }).fill('https://novels.example.com/book/1')
  await page.getByRole('button', { name: 'Start import' }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'exit code 1' })).toBeVisible()
  await page.getByText('What lightnovel-crawler printed').click()
  await expect(page.getByText('No crawler found for https://novels.example.com')).toBeVisible()
})
