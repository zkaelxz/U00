import { expect, test } from '@playwright/test'

// Chapter OCR: the Slice 38 endpoints are mocked (no OCR engine); the drama
// read hits the real seeded API.

const png = { name: 'p1.png', mimeType: 'image/png', buffer: Buffer.from('x') }

test('chapter OCR uploads images, shows the job with cancel, and reloads status', async ({ page }) => {
  let body = ''
  let started = false
  await page.route('**/api/novel/dramas/1/ocr-chapter', async (route) => {
    body = route.request().postData() ?? ''
    started = true
    await route.fulfill({ json: { job_id: 'ocrchapter_1' } })
  })
  await page.route('**/api/jobs/ocrchapter_1', (route) =>
    started ? route.fulfill({
      json: { job_id: 'ocrchapter_1', status: 'running', progress: 0.4, message: 'page 2 of 5', error: null },
    }) : route.fulfill({ status: 404, json: { detail: 'not found' } }),
  )
  await page.goto('/#/drama/1/source')
  await page.locator('.section-title', { hasText: 'Novel text' }).click()
  await page.locator('.section-title', { hasText: 'Chapter images (OCR)' }).click()
  const run = page.getByRole('button', { name: 'Extract text from images' })
  await expect(run).toBeDisabled()
  await page.getByLabel('Page images', { exact: true }).setInputFiles([png])
  await expect(run).toBeEnabled()
  await run.click()
  await expect(page.getByTestId('job-status')).toContainText('running')
  await expect(page.getByRole('button', { name: /cancel/i })).toBeVisible()
  expect(body).toContain('name="backend"')
  expect(body).toContain('p1.png')
})

test('unsupported image types are rejected client-side', async ({ page }) => {
  await page.goto('/#/drama/1/source')
  await page.locator('.section-title', { hasText: 'Novel text' }).click()
  await page.locator('.section-title', { hasText: 'Chapter images (OCR)' }).click()
  await page.getByLabel('Page images', { exact: true }).setInputFiles([{ name: 'a.gif', mimeType: 'image/gif', buffer: Buffer.from('x') }])
  await expect(page.getByRole('alert').filter({ hasText: 'a.gif' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Extract text from images' })).toBeDisabled()
})
