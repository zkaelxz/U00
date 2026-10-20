import { expect, test, type Page } from '@playwright/test'

import { TRACKED, mockAccess, openProxy } from './sourcesAccessMocks'
import { mockSources, openManage } from './sourcesMocks'
import { installHitArea, expectTargetsAtLeast44 } from './hitArea'
import { expectNoSidewaysScroll } from './mockHelpers'

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// Phone project (390x844, touch): New chapters with Check now and the
// auto-import drama, a source's sign-in and tier tests, and the proxy field.

async function tallTargets(page: Page, root: string) {
  await expectTargetsAtLeast44(
    page.locator(root),
    'button:not(.field-help-btn), select, input[type="url"], .sources-autoimport',
  )
}

test('phone: check now and the auto-import title fit, 44 px targets', async ({ page }) => {
  const s = await mockSources(page, { tracked: TRACKED })
  await mockAccess(page, s)
  await page.goto('/#/sources?tab=new')
  const news = page.getByRole('region', { name: 'New chapters' })
  await news.getByRole('button', { name: 'Check now' }).click()
  await expect(news.getByText('Checked 1 series · 1 new chapter.')).toBeVisible({ timeout: 15_000 })
  await expect(news.getByRole('combobox', { name: 'Auto-import into' })).toBeEnabled()
  await expectNoSidewaysScroll(page)
  await tallTargets(page, '.sources-tracked, .sources-check, .sources-new .card-actions')
  await page.screenshot({ path: 'test-results/sources-new-chapters-phone.png', fullPage: true })
  expect(s.unmocked).toEqual([])
})

test('phone: sign-in, tier tests and proxy in the source card', async ({ page }) => {
  const s = await mockSources(page)
  await mockAccess(page, s)
  const settings = await openManage(page)
  const card = settings.locator('ul.source-cards > li').nth(1)
  await card.getByRole('button', { name: 'Details: Beta Novels' }).click()
  const access = settings.getByRole('group', { name: 'Access tests and sign-in: Beta Novels' })
  await access.getByRole('textbox', { name: 'Page on this site' }).fill('https://beta.example/book/1')
  await access.getByRole('button', { name: 'Static' }).click()
  await expect(access.getByText('Static: works.')).toBeVisible({ timeout: 15_000 })
  await expectNoSidewaysScroll(page)
  await tallTargets(page, '.source-access')
  await page.goto('/#/sources/manage?tab=network')
  await openProxy(page)
  await tallTargets(page, '.sources-proxy')
  await expectNoSidewaysScroll(page)
  await page.screenshot({ path: 'test-results/sources-access-phone.png', fullPage: true })
  expect(s.unmocked).toEqual([])
})
