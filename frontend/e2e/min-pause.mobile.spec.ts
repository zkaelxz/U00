import { expect, test } from '@playwright/test'
import { editPauseAndSave } from './minPauseHelpers'

// Phone project (390x844, touch): the pause field saves and the page does not scroll sideways.

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

test('editing the pause and saving works on a phone without sideways scroll', async ({ page }) => {
  expect((await editPauseAndSave(page)).min_pause_sec).toBe(0.6)
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
})
