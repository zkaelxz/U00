import { expect, test, type Page } from '@playwright/test'
import { inSeriesSeven, mockLanguagePacks } from './languagePacksMocks'

// Phone project (390x844): the Language packs section fits the width and keeps 44px touch targets.

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

test('fits the phone width with 44px targets', async ({ page }: { page: Page }) => {
  await inSeriesSeven(page)
  await mockLanguagePacks(page)
  await page.goto('/#/drama/1/translate')
  for (const title of ['Glossary', 'Language packs']) {
    const summary = page.locator('summary').filter({ has: page.locator('.section-title', { hasText: new RegExp(`^${title}$`) }) }).first()
    if ((await summary.locator('xpath=..').getAttribute('open')) === null) await summary.click()
  }
  await page.getByText('View entries (2)').first().click()
  await expect(page.getByRole('button', { name: 'Add 先輩 to my glossary' })).toBeVisible()
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
  for (const loc of [
    page.getByRole('button', { name: 'Add 先輩 to my glossary' }),
    page.getByRole('button', { name: 'Use this choice for Japanese titles' }),
    page.getByLabel('How to write them'),
  ]) {
    const box = await loc.boundingBox()
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(44)
  }
})
