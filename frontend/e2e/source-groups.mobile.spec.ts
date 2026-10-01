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
