import { test } from './fixtures'
import { resetReadingSpeed, runReadingSpeedScenario, seedReadingSpeed } from './readingSpeedScenario'

// Reading speed check setting and the bulk clear, against the real seeded API.

test.beforeEach(() => seedReadingSpeed())
test.afterAll(() => resetReadingSpeed())

test('reading speed check setting, re-check and clear', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await runReadingSpeedScenario(page)
})
