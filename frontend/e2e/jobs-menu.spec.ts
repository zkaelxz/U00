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

test('no badge at zero, and All jobs opens Diagnostics', async ({ page }) => {
  await mockJobs(page, [])
  await page.goto('/#/library')
  const button = page.getByRole('button', { name: 'Jobs', exact: true })
  await expect(button).toBeVisible()
  await expect(page.getByTestId('jobs-count')).toHaveCount(0)
  await button.click()
  await expect(page.getByText('No jobs yet.')).toBeVisible()
  await page.getByRole('link', { name: 'All jobs' }).click()
  await expect(page).toHaveURL(/#\/diagnostics$/)
})

test('the button hides when the viewer may not list jobs', async ({ page }) => {
  await page.route((u) => u.pathname === '/api/jobs', (route) =>
    route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ error: { code: 'forbidden', message: 'No.' } }) }),
  )
  await page.goto('/#/library')
  await expect(page.getByRole('button', { name: /^Notifications/ })).toBeVisible()
  await expect(page.getByRole('button', { name: /^Jobs/ })).toHaveCount(0)
})
