import { expect, test } from '@playwright/test'

test('Source stage groups fit a phone without sideways scroll', async ({ page }, info) => {
  await page.goto('/#/drama/2/source')
  await expect(page.getByRole('region', { name: 'Novel text' })).toBeVisible()
  const wide = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)
  expect(wide).toBe(false)
  const summary = page.locator('.stage-source > details.section > summary').first()
  expect((await summary.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  await page.screenshot({ path: `${process.env.SHOT_DIR ?? info.outputDir}/source-phone.png`, fullPage: true })
})

test('the optional novel row on an audio drama fits a phone with a 44px row', async ({ page }, info) => {
  await page.route('**/api/novel/dramas/1/status', (r) => r.fulfill({ json: { drama_id: 1, has_novel_text: false, char_count: 0, chapters: 0, ocr_running: false } }))
  await page.route('**/api/novel/dramas/1/raw-novel', (r) => r.fulfill({ json: { drama_id: 1, present: false, size_bytes: 0, char_count: 0 } }))
  await page.addInitScript(() => {
    for (const k of Object.keys(localStorage)) if (k.startsWith('baihe.section.')) localStorage.removeItem(k)
  })
  await page.goto('/#/drama/1/source')
  const row = page.locator('summary', { has: page.locator('.section-title', { hasText: /^Attach novel text \(optional\)$/ }) })
  expect((await row.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  await row.click()
  await expect(page.getByRole('button', { name: 'Attach text' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(false)
  await page.screenshot({ path: `${process.env.SHOT_DIR ?? info.outputDir}/source-phone-optional-novel.png`, fullPage: true })
})
