import { expect, test } from '@playwright/test'

import { mockJobsApi, pageJobs } from './jobsMenuMocks'
import { navLink, openMenu } from './settingsNav'

// Below 1024px the Jobs page is reached through the Menu drawer, not the left rail.
test('phone: the Menu drawer lists Jobs and clicking it opens the page', async ({ page }) => {
  await mockJobsApi(page, pageJobs(), true)
  await page.goto('/#/library')
  await openMenu(page)
  const link = navLink(page, 'Jobs')
  await expect(link).toBeVisible()
  await link.click()
  await expect(page).toHaveURL(/#\/jobs$/)
  await expect(page.getByRole('heading', { name: 'Jobs' })).toBeVisible()
})
