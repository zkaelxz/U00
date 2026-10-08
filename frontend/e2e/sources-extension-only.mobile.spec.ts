import { expect, test } from '@playwright/test'

import { installHitArea } from './hitArea'
import { mockExtensionOnly } from './sourcesExtensionMocks'
import { mockSources, posted } from './sourcesMocks'

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// Phone project (390x844, touch): the extension-only marker on a source card.

test('phone: mark a source as extension only, 44 px targets, no sideways scroll', async ({ page }) => {
  const s = await mockSources(page)
  await mockExtensionOnly(page, s)
  await page.goto('/#/sources')
  const settings = page.getByRole('region', { name: 'Source settings' })
  await settings.getByText('Source settings').first().click()
  const card = settings.getByRole('listitem').filter({ hasText: 'Alpha Comics' })
  await card.getByRole('button', { name: 'Details' }).click()
  await card.getByRole('switch', { name: 'Works only with the browser extension' }).click()
  await expect(card.locator('.source-dl')).toContainText('Extension only')
  await expect(card.locator('.source-dl')).toContainText('You in a browser: works (marked by you, 2026-10-08)')
  expect(posted(s, '/api/sources/alpha/extension-only')).toHaveLength(1)

  const small = await page.locator('.source-extension').evaluateAll((els) =>
    els.flatMap((el) => [...el.querySelectorAll<HTMLElement>('button:not(.field-help-btn):not(.toggle), input[type="text"], .field-item')])
      .filter((e) => e.offsetParent !== null)
      .map((e) => ({ h: window.hitHeight(e), text: (e.textContent || e.getAttribute('aria-label') || '').trim() }))
      .filter((x) => x.h < 44))
  expect(small).toEqual([])
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
  expect(s.unmocked).toEqual([])
})
