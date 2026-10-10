import { expect, test, type Locator, type Page } from '@playwright/test'

// Phone project (390x844, touch): the lightnovel-crawler import fits the
// width and keeps 44px touch targets. lncrawl status is mocked as installed.

async function expectTall(loc: Locator) {
  await expect(loc).toBeVisible()
  const box = await loc.boundingBox()
  expect(box?.height ?? 0).toBeGreaterThanOrEqual(44)
}

async function expectNoHorizontalOverflow(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

test('lightnovel-crawler import fits a phone with 44px controls', async ({ page }) => {
  await page.route('**/api/novel/lncrawl', (route) => route.fulfill({ json: { installed: true, path_configured: false } }))
  await page.goto('/#/drama/1/source')
  await page.locator('.section-title', { hasText: /^Attach novel text \(optional\)$/ }).click()
  await page.locator('.section-title', { hasText: /^Import with lightnovel-crawler$/ }).click()
  await page.getByLabel('Novel address', { exact: true }).fill('https://novels.example.com/book/1')
  await page.getByLabel('Chapters', { exact: true }).selectOption('last')
  await expectTall(page.getByRole('button', { name: 'Start import' }))
  await expectTall(page.getByLabel('Novel address', { exact: true }))
  await expectTall(page.getByLabel('Chapters', { exact: true }))
  await expectTall(page.getByLabel('How many', { exact: true }))
  await expectNoHorizontalOverflow(page)
})
