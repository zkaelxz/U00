import { expect, test } from './fixtures'
import { openAi } from './reviewFolds'
import { clearReviewResults, seedReviewResults } from './reviewResultsSeed'

// Review in a small desktop window with the labelled side menu docked: the menu takes 15rem, so the page
// gets less room than the window width suggests and nothing may run past its right edge.

test.beforeEach(() => seedReviewResults())
test.afterAll(() => clearReviewResults())

test('a 760px window with the side menu docked: no sideways scroll, Export tab and the Bulk switches on screen', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('baihe.navDock', 'docked'))
  await page.setViewportSize({ width: 760, height: 900 })
  await page.goto('/#/drama/3/review')
  const ai = await openAi(page)
  await expect(ai.getByRole('switch').first()).toBeVisible()
  const width = await page.evaluate(() => document.documentElement.clientWidth)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  const rightEdges = [page.getByRole('link', { name: /Export/ }).first(), ...(await ai.getByRole('switch').all())]
  for (const el of rightEdges) {
    const box = (await el.boundingBox())!
    expect(box.x + box.width).toBeLessThanOrEqual(width)
  }
})
