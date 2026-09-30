import { expect, test } from '@playwright/test'

// Phone layout of the new Settings sections: open every one, no sideways
// scroll, and the buttons are at least 44px tall. Reads come from the real
// seeded API; any write is aborted and recorded.

// Cards are always open; the Advanced Card's Sections are opened here.
const CARDS = ['Defaults for new dramas', 'Spending']
const SECTIONS = ['OCR', 'Offline and performance', 'Downloads', 'Server addresses']

test.afterEach(async ({ page }) => {
  await page.evaluate(() => {
    for (const k of Object.keys(localStorage)) if (k.startsWith('baihe.section.settings.')) localStorage.removeItem(k)
  })
})

test('settings preference sections fit a phone with 44px targets', async ({ page }) => {
  const unmocked: string[] = []
  await page.route('**/api/**', (route) => {
    const r = route.request()
    if (r.method() === 'GET') return route.fallback()
    unmocked.push(`${r.method()} ${r.url()}`)
    return route.abort()
  })
  await page.goto('/#/settings')
  for (const title of [...CARDS, ...SECTIONS]) {
    let s = page.getByRole('region', { name: title, exact: true })
    if (SECTIONS.includes(title)) {
      s = page.locator('details.section', { has: page.locator('.section-title', { hasText: new RegExp(`^${title}$`) }) })
      await s.locator('summary').click()
      await expect(s).toHaveAttribute('open', '')
    }
    await expect(s).toBeVisible()
    for (const b of await s.getByRole('button', { name: /^(Save|Clear)$/ }).all()) {
      const box = await b.boundingBox()
      expect(box && box.height).toBeGreaterThanOrEqual(44)
    }
  }
  await expect(page.getByLabel('Translation engine', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.screenshot({ path: 'test-results/settings-preferences-phone.png', fullPage: true })
  expect(unmocked).toEqual([])
})
