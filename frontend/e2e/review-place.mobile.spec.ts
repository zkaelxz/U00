import { clearCheckLines, seedCheckLines } from './compareSelectedHelpers'
import { expectNoSidewaysScroll } from './mockHelpers'
import { expect, test } from './fixtures'

// The check queue at phone width reopens on the card it was left on. Title 3 gets 100 lines; three of them need a look
// (#24, #7 and #12, in that order).

test.afterAll(() => clearCheckLines(3))

test('the queue reopens on the kept card at 390 px, with no sideways scroll, and on its first card once that one is checked', async ({ page }) => {
  const ids = seedCheckLines({ count: 100 })
  await page.addInitScript(() => localStorage.setItem('baihe.pref.title.view', '"simple"'))
  await page.goto('/#/drama/3/review')
  const position = page.getByTestId('queue-position')
  const cards = page.locator('.review-line')
  const bar = page.getByRole('toolbar', { name: 'Line actions' })
  await expect(position).toHaveText('1 of 3 to look at')
  await bar.getByRole('button', { name: 'Skip for now' }).tap()
  await expect(position).toHaveText('2 of 3 to look at')
  await expect.poll(() => page.evaluate(() => localStorage.getItem('baihe.pref.review.at.3'))).toContain('"view":"queue"')

  await page.goto('/#/library')
  await expect(cards).toHaveCount(0)
  await page.goto('/#/drama/3/review')
  await expect(position).toHaveText('2 of 3 to look at')
  await expect(cards.locator('.review-idx')).toHaveText('#7')
  await expect(page.locator('.review-status')).toHaveText('Back where you stopped, on line 7.')
  await expectNoSidewaysScroll(page)

  const checked = await page.request.post(`/api/lines/dramas/3/lines/${ids[6]}/reviewed`, { data: { reviewed: true } })
  expect(checked.ok()).toBe(true)
  await page.goto('/#/library')
  await expect(cards).toHaveCount(0)
  await page.goto('/#/drama/3/review')
  await expect(position).toHaveText('1 of 2 to look at')
  await expect(cards.locator('.review-idx')).toHaveText('#24')
  await expectNoSidewaysScroll(page)
})
