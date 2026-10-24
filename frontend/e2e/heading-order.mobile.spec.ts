import { test } from './fixtures'
import { expectHeadingOrderOnEveryTab } from './headingOrder'

// The phone layout has its own title strip, bottom bar and reader header; its headings step down one level at a
// time too (heading-order.spec.ts has the why).

for (const hash of ['#/', '#/settings', '#/jobs', '#/read/2', '#/comic/2', '#/drama/1/source', '#/drama/3/translate', '#/drama/3/export']) {
  test(`phone headings never skip a level: ${hash}`, async ({ page }) => {
    await page.goto(`/${hash}`)
    await page.locator('main h1').first().waitFor({ state: 'attached' })
    await expectHeadingOrderOnEveryTab(page)
  })
}
