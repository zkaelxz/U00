import { expect, test } from '@playwright/test'

import { mockFirstRun } from './getStartedMocks'

test('the card fits a phone with 44px targets', async ({ page }) => {
  await mockFirstRun(page)
  await page.goto('/')
  const card = page.getByRole('region', { name: 'Get started' })
  await expect(card).toBeVisible()
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
  for (const el of [
    card.getByRole('button', { name: 'Dismiss' }),
    card.getByRole('button', { name: 'New drama', exact: true }),
    card.getByRole('link', { name: 'Discover' }),
    card.getByRole('radio', { name: /Ollama/ }),
  ]) {
    const box = await el.evaluate((n) => {
      const t = n.closest('label') ?? n
      const r = t.getBoundingClientRect()
      return { w: r.width, h: r.height }
    })
    expect(box.h).toBeGreaterThanOrEqual(44)
  }
})
