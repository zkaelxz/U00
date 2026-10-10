import { expect, test } from '@playwright/test'
import { pickSensitiveAndSave } from './sensitivityHelpers'

// Phone project (390x844, touch): the preset saves and the page does not scroll sideways.

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

test('choosing More sensitive works on a phone without sideways scroll', async ({ page }) => {
  expect((await pickSensitiveAndSave(page)).sensitivity_preset).toBe('sensitive')
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
})
