import { expect, test } from '@playwright/test'

import { mockJobsApi, pageJobs } from './jobsMenuMocks'
import { openGear } from './settingsNav'

// Below 1024px the Jobs page is reached through the cogwheel menu, not the left rail.
test('phone: the cogwheel menu lists Jobs and clicking it opens the page', async ({ page }) => {
  await mockJobsApi(page, pageJobs(), true)
  await page.goto('/#/library')
  await expect(page.locator('summary[aria-label="Settings and tools"]')).toBeVisible()
  await openGear(page)
  const link = page.getByRole('group', { name: 'Settings and tools pages' }).getByRole('link', { name: /^Jobs/ })
  await expect(link).toBeVisible()
  await link.click()
  await expect(page).toHaveURL(/#\/jobs$/)
  await expect(page.getByRole('heading', { name: 'Jobs' })).toBeVisible()
})
