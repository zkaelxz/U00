import { expect, test, type Page } from '@playwright/test'

import { withExportLines, withTranslateLines } from './stageLineMocks'

// Leaving a stage while its job runs and coming back picks the job up again
// (useReattachJob). Job starts and job reads are mocked; nothing runs.

const job = (id: string, status: string) => ({
  job_id: id, status, progress: 0.4, message: 'working', error: null, description: null,
  gpu_touching: false, started_at: 1, finished_at: null, updated_at: 1,
})

const goToStage = (page: Page, name: RegExp) =>
  page.getByRole('navigation', { name: 'Stages' }).getByRole('link', { name }).click()

test('a translate job keeps showing after leaving the stage and coming back', async ({ page }) => {
  const starts: unknown[] = []
  await page.route('**/api/translate-run/dramas/1/run', async (route) => {
    starts.push(route.request().postDataJSON())
    await route.fulfill({
      json: { job_id: 'translate_1', drama_id: 1, engine: 'x', model: null, target_line_count: 3, fallback_engines: [] },
    })
  })
  // No such job until Start is pressed (the real API answers 404).
  await page.route('**/api/jobs/translate_1', (route) =>
    starts.length
      ? route.fulfill({ json: job('translate_1', 'running') })
      : route.fulfill({ status: 404, json: { error: { code: 'not_found', message: 'No job.' } } }))
  await withTranslateLines(page)

  await page.goto('/#/drama/1/translate')
  const primary = page.getByRole('region', { name: 'Translate run' }).getByRole('button', { name: /^Translate \d+ lines?$/ })
  await primary.click()
  await expect(page.getByTestId('job-status')).toContainText('running')
  await expect(primary).toBeDisabled()

  await goToStage(page, /^Dub/)
  await expect(page).toHaveURL(/\/drama\/1\/dub$/)
  await goToStage(page, /^Translate/)

  await expect(page.getByTestId('job-status')).toContainText('running')
  await expect(primary).toBeDisabled()
  await expect(primary).toHaveAccessibleDescription(/A translate job is running/)
  expect(starts).toHaveLength(1)
})

test('a finished or missing translate job does not block Start on a fresh visit', async ({ page }) => {
  await page.route('**/api/jobs/translate_1', (route) =>
    route.fulfill({ json: { ...job('translate_1', 'done'), finished_at: 2 } }))
  await withTranslateLines(page)
  await page.goto('/#/drama/1/translate')
  await expect(page.getByRole('region', { name: 'Translate run' }).getByRole('button', { name: /^Translate \d+ lines?$/ })).toBeEnabled()
  await expect(page.getByTestId('job-status')).toHaveCount(0)
})

test('Export media shows the earlier export and a running export on revisit', async ({ page }) => {
  await page.route('**/api/artifacts/dramas/1/video/info', (route) =>
    route.fulfill({ json: { name: 'burned_video_1.mp4', size: 3 * 1024 * 1024, kind: 'video' } }))
  await page.route('**/api/jobs/audiobook_1', (route) => route.fulfill({ json: job('audiobook_1', 'running') }))
  await withExportLines(page)

  await page.goto('/#/drama/1/translate')
  await goToStage(page, /^Export/)
  await page.getByText('More export', { exact: true }).click()

  const link = page.getByTestId('artifact-video').getByRole('link')
  await expect(link).toHaveText('Download burned_video_1.mp4')
  await expect(link).toHaveAttribute('href', /\/api\/artifacts\/dramas\/1\/video$/)

  const audiobook = page.getByRole('group', { name: 'Audiobook' })
  await expect(audiobook.getByTestId('job-status')).toContainText('running')
  await expect(audiobook.getByRole('button', { name: 'Start audiobook export' })).toBeDisabled()
})
