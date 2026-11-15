import { expect, test } from './fixtures'

// A phone's header keeps its icons: no words, no Connected, and a failed check still says so.

const metaAnswer = (page: import('@playwright/test').Page) => page.waitForResponse((r) => new URL(r.url()).pathname === '/api/meta')

test('phone: icons only, no Connected word, and a failed check still shows the badge', async ({ page }) => {
  let failing = false
  await page.route((u) => u.pathname === '/api/meta', (route) => (failing ? route.abort() : route.fallback()))
  await page.goto('/#/library')
  await expect(page.locator('.header-end')).toBeVisible()
  // A check answered on a later focus, so the state has certainly been set when the absence is asserted.
  const answered = metaAnswer(page)
  await page.evaluate(() => window.dispatchEvent(new Event('focus')))
  await answered
  await expect(page.getByTestId('api-connected')).toHaveCount(0)
  await expect(page.locator('.header-end .hb-words:visible')).toHaveCount(0)

  failing = true
  // A check still in flight from before the switch can land last, so ask again until the failure sticks.
  await expect(async () => {
    await page.evaluate(() => window.dispatchEvent(new Event('focus')))
    await expect(page.getByTestId('api-status')).toBeVisible({ timeout: 1000 })
  }).toPass()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)
})

test('a touch screen wide enough for words: the search button hides its key hint, and its dot with it', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/#/library')
  const search = page.locator('.header-end button.palette-btn')
  await expect(search.locator('.hb-words')).toBeVisible()
  await expect(search.locator('kbd')).toBeHidden()
  const text = await search.evaluate((el) => (el as HTMLElement).innerText.replace(/\s+/g, ' ').trim())
  expect(text).toBe('Search pages and titles')
})
