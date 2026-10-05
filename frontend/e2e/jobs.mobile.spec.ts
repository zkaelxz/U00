import { expect, test } from './fixtures'
import { hitHeight, installHitArea } from './hitArea'
import { mockJobsApi, pageJobs } from './jobsMenuMocks'

// The Jobs page on a phone (390x844, touch): cards instead of a table, no
// sideways scroll, 44px targets, tap through to a stage.

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

async function noSideways(page: import('@playwright/test').Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

const long = 'x'.repeat(300)

test('phone: cards with title, status, progress and 44px actions, no sideways scroll', async ({ page }) => {
  const jobs = pageJobs()
  jobs[3] = { ...jobs[3], description: `Export Kae ${long}`, error: `Provider failed: ${long}` }
  const log = await mockJobsApi(page, jobs, true)
  await page.goto('/#/jobs')
  await expect(page.getByTestId('jobs-table')).toHaveCount(0)
  const cards = page.getByTestId('jobs-cards').locator('> li')
  await expect(cards).toHaveCount(2)
  const first = cards.first()
  await expect(first.getByRole('link', { name: 'Signal', exact: true })).toBeVisible()
  await expect(first).toContainText('Translate Signal')
  await expect(first.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '40')
  await expect(first).toContainText('40%')

  await page.getByRole('group', { name: 'Status' }).getByRole('button', { name: /^All/ }).click()
  await expect(cards).toHaveCount(5)
  await page.getByRole('button', { name: 'Details for Transcribe Signal' }).click()
  await expect(page.getByTestId('job-details-transcribe_3')).toBeVisible()

  // Every control is a 44px target (dense buttons keep a 44px hit area).
  const targets = await page.locator('.jobs-page button, .jobs-page a, .jobs-page select, .jobs-page input')
    .evaluateAll((els) => els.filter((e) => (e as HTMLElement).offsetParent !== null)
      .map((e) => ({ h: window.hitHeight(e), text: (e.textContent || (e as HTMLInputElement).placeholder || e.tagName).trim().slice(0, 30) })))
  expect(targets.length).toBeGreaterThan(8)
  expect(targets.filter(({ h }) => h < 44)).toEqual([])
  await noSideways(page)

  // Cancel is on the running card (back on Active), as a 44px target.
  await page.getByRole('group', { name: 'Status' }).getByRole('button', { name: /^Active/ }).click()
  const cancel = first.getByRole('button', { name: 'Cancel Translate Signal' })
  expect(await hitHeight(cancel)).toBeGreaterThanOrEqual(44)
  await cancel.tap()
  await expect.poll(() => log.cancelled).toEqual(['translate_3'])
  if (process.env.JOBS_SCREENS_DIR) await page.screenshot({ path: `${process.env.JOBS_SCREENS_DIR}/jobs-page-phone.png`, fullPage: true })
})

test('phone: tap through from a job to its stage', async ({ page }) => {
  await mockJobsApi(page, pageJobs(), true)
  await page.goto('/#/jobs')
  await page.getByRole('link', { name: 'Open translate for Translate Signal' }).tap()
  await expect(page).toHaveURL(/#\/drama\/3\/translate$/)
})

test('phone: a failed job with a long name and error fits', async ({ page }) => {
  const jobs = pageJobs()
  jobs[3] = { ...jobs[3], description: `Export Kae ${long}`, error: `Provider failed: ${long}` }
  await mockJobsApi(page, jobs, true)
  await page.goto('/#/jobs')
  await page.getByRole('group', { name: 'Status' }).getByRole('button', { name: /^Failed/ }).click()
  await expect(page.getByTestId('jobs-cards').locator('> li')).toHaveCount(1)
  await noSideways(page)
})

test('phone: the popover job name opens its stage', async ({ page }) => {
  await mockJobsApi(page, pageJobs())
  await page.goto('/#/library')
  await page.getByRole('button', { name: /^Jobs \(/ }).tap()
  const panel = page.getByRole('region', { name: 'Jobs' })
  await panel.getByRole('link', { name: 'Translate Signal' }).tap()
  await expect(page).toHaveURL(/#\/drama\/3\/translate$/)
})
