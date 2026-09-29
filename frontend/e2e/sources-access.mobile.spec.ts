import { expect, test, type Page } from '@playwright/test'

import { TRACKED, mockAccess } from './sourcesAccessMocks'
import { mockSources } from './sourcesMocks'

// Phone project (390x844, touch): New chapters with Check now and the
// auto-import drama, a source's sign-in and tier tests, and the proxy field.

async function noSideways(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

async function tallTargets(page: Page, root: string) {
  const small = await page.locator(root).evaluateAll((els) => {
    const sel = 'button:not(.field-help-btn), select, input[type="url"], .sources-autoimport'
    return els.flatMap((el) => [...el.querySelectorAll<HTMLElement>(sel)])
      .filter((e) => e.offsetParent !== null)
      .map((e) => ({ h: e.getBoundingClientRect().height, text: (e.textContent || e.getAttribute('aria-label') || e.tagName).trim().slice(0, 30) }))
      .filter((x) => x.h < 44)
  })
  expect(small).toEqual([])
}

test('phone: check now and the auto-import drama fit, 44 px targets', async ({ page }) => {
  const s = await mockSources(page, { tracked: TRACKED })
  await mockAccess(page, s)
  await page.goto('/#/sources')
  const news = page.getByRole('region', { name: 'New chapters' })
  await news.getByRole('button', { name: 'Check now' }).click()
  await expect(news.getByText('Checked 1 series · 1 new chapter.')).toBeVisible({ timeout: 15_000 })
  await expect(news.getByRole('combobox', { name: 'Auto-import into' })).toBeEnabled()
  await noSideways(page)
  await tallTargets(page, '.sources-tracked, .sources-check, .sources-new .card-actions')
  await page.screenshot({ path: 'test-results/sources-new-chapters-phone.png', fullPage: true })
  expect(s.unmocked).toEqual([])
})

test('phone: sign-in, tier tests and proxy in the source card', async ({ page }) => {
  const s = await mockSources(page)
  await mockAccess(page, s)
  await page.goto('/#/sources')
  const settings = page.getByRole('region', { name: 'Source settings' })
  await settings.getByText('Source settings').first().click()
  const card = settings.locator('ul.source-cards > li').nth(1)
  await card.getByRole('button', { name: 'Details' }).click()
  const access = settings.getByRole('group', { name: 'Access tests and sign-in: Beta Novels' })
  await access.getByRole('textbox', { name: 'Page on this site' }).fill('https://beta.example/book/1')
  await access.getByRole('button', { name: 'Static' }).click()
  await expect(access.getByText('Static: works.')).toBeVisible({ timeout: 15_000 })
  await noSideways(page)
  await tallTargets(page, '.source-access')
  await tallTargets(page, '.sources-proxy')
  await page.screenshot({ path: 'test-results/sources-access-phone.png', fullPage: true })
  expect(s.unmocked).toEqual([])
})
