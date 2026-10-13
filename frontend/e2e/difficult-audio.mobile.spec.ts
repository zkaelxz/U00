import { expect, test } from '@playwright/test'
import { turnDifficultAudioOnAndSave } from './difficultAudioHelpers'

// Phone project (390x844, touch): the switch saves and the page does not scroll sideways.

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

test('turning Difficult audio on works on a phone without sideways scroll', async ({ page }) => {
  expect((await turnDifficultAudioOnAndSave(page)).difficult_audio).toBe(true)
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
})
