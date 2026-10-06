import { expect, test } from '@playwright/test'

import { mockJobs } from './jobsMenuMocks'

// Phone (390x844, touch): the Jobs button is a 44px target, the header does
// not scroll sideways and the panel fits the screen.
test('phone: Jobs button is 44px and the panel fits', async ({ page }) => {
  await mockJobs(page)
  await page.goto('/#/library')
  const button = page.getByRole('button', { name: 'Jobs (2 jobs running)' })
  await expect(button).toBeVisible()
  const box = await button.boundingBox()
  expect(box!.width).toBeGreaterThanOrEqual(44)
  expect(box!.height).toBeGreaterThanOrEqual(44)
  await button.tap()
  const panel = page.getByRole('region', { name: 'Jobs' })
  await expect(panel).toBeVisible()
  const p = await panel.boundingBox()
  expect(p!.x).toBeGreaterThanOrEqual(0)
  expect(p!.x + p!.width).toBeLessThanOrEqual(390)
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll).toBeLessThanOrEqual(client)
  const cancel = await panel.getByRole('button', { name: /^Cancel/ }).first().boundingBox()
  expect(cancel!.height).toBeGreaterThanOrEqual(44)
  if (process.env.JOBS_SCREENS_DIR) await page.screenshot({ path: `${process.env.JOBS_SCREENS_DIR}/jobs-phone.png` })
})
