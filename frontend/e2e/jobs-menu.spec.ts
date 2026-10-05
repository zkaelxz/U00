import { expect, test } from './fixtures'
import { mockJobs } from './jobsMenuMocks'

// The header Jobs button with /api/jobs mocked: badge, panel, Cancel, "All jobs".

test('badge counts active jobs and the panel lists active first', async ({ page }) => {
  const cancelled = await mockJobs(page)
  await page.goto('/#/library')
  const button = page.getByRole('button', { name: 'Jobs (2 jobs running)' })
  await expect(button).toBeVisible()
  await expect(page.getByTestId('jobs-count')).toHaveText('2')
  await button.click()
  const panel = page.getByRole('region', { name: 'Jobs' })
  const names = await panel.locator('.jobs-item-name').allTextContents()
  expect(names).toEqual([
    'Transcribe Episode 12 with a rather long title',
    'Dub Episode 3',
    'Translate finished episode',
    'Export broken',
  ])
  await expect(panel.getByText('Running', { exact: true })).toBeVisible()
  await expect(panel.getByText('Failed', { exact: true })).toBeVisible()
  await expect(panel.getByRole('button', { name: /Delete/ })).toHaveCount(0)
  await panel.getByRole('button', { name: 'Cancel Dub Episode 3' }).click()
  await expect.poll(() => cancelled).toEqual(['q1'])
  await page.keyboard.press('Escape')
  await expect(panel).toBeHidden()
})

test('no badge at zero, and All jobs opens the Jobs page', async ({ page }) => {
  await mockJobs(page, [])
  await page.goto('/#/library')
  const button = page.getByRole('button', { name: 'Jobs', exact: true })
  await expect(button).toBeVisible()
  await expect(page.getByTestId('jobs-count')).toHaveCount(0)
  await button.click()
  await expect(page.getByText('No jobs yet.')).toBeVisible()
  await page.getByRole('link', { name: 'All jobs' }).click()
  await expect(page).toHaveURL(/#\/jobs$/)
})

test('the button hides when the viewer may not list jobs', async ({ page }) => {
  await page.route((u) => u.pathname === '/api/jobs', (route) =>
    route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ error: { code: 'forbidden', message: 'No.' } }) }),
  )
  await page.goto('/#/library')
  await expect(page.getByRole('button', { name: /^Notifications/ })).toBeVisible()
  await expect(page.getByRole('button', { name: /^Jobs/ })).toHaveCount(0)
})

test('a job name in the panel links to its stage, or to the Jobs page when it has no title', async ({ page }) => {
  await mockJobs(page, [
    { job_id: 'translate_3', status: 'running', progress: null, message: '', error: null, description: 'Translate Signal', gpu_touching: false, started_at: 1, finished_at: null, updated_at: 1, owned_by_me: true, drama_id: 3, kind: 'translate' },
    { job_id: 'lncrawl_9', status: 'running', progress: null, message: '', error: null, description: 'Import novel', gpu_touching: false, started_at: 1, finished_at: null, updated_at: 1, owned_by_me: true, drama_id: null, kind: 'other' },
  ])
  await page.goto('/#/library')
  await page.getByRole('button', { name: 'Jobs (2 jobs running)' }).click()
  const panel = page.getByRole('region', { name: 'Jobs' })
  await expect(panel.getByRole('link', { name: 'Translate Signal' })).toHaveAttribute('href', '#/drama/3/translate')
  await expect(panel.getByRole('link', { name: 'Import novel' })).toHaveAttribute('href', '#/jobs')
  await panel.getByRole('link', { name: 'Translate Signal' }).click()
  await expect(page).toHaveURL(/#\/drama\/3\/translate$/)
  await expect(panel).toBeHidden()
})
