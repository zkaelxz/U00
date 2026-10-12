import { expect, test } from '@playwright/test'
import { glossaryTab, type GlossaryTabName } from './glossaryTabs'
import { glossarySetup, heldScan, prop } from './glossaryScanMocks'
import { gotoTranslate } from './translateTabs'

// Phone project (390x844): the tab row scrolls inside itself, every tab fits the width with
// 44px targets, and the scan banner fits.

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

const TABS: GlossaryTabName[] = ['Terms', 'Find terms', 'Names', 'Instructions', 'Re-translate']

for (const scheme of ['light', 'dark'] as const) {
  test(`every Glossary tab fits the phone (${scheme})`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: scheme })
    await glossarySetup(page)
    const lines = heldScan({ status: 'done', progress: 1, proposals: [prop('魏婴', 'Wei Ying')] })
    await page.route('**/api/glossary/dramas/1/from-lines', lines.handler)
    await gotoTranslate(page, 'translate')
    const banner = page.getByTestId('glossary-scan-banner')
    await expect(banner).toContainText('Scan finished: 1 proposal to review')
    const bannerBox = await banner.getByRole('button').boundingBox()
    expect(bannerBox!.height).toBeGreaterThanOrEqual(44)
    for (const name of TABS) {
      await glossaryTab(page, name)
      const { scroll, client } = await page.evaluate(() => ({
        scroll: document.documentElement.scrollWidth,
        client: document.documentElement.clientWidth,
      }))
      expect(scroll, `${name} scrolls sideways`).toBeLessThanOrEqual(client)
    }
    for (const tab of await page.getByRole('region', { name: 'Glossary' }).getByRole('tab').all()) {
      expect((await tab.boundingBox())!.height).toBeGreaterThanOrEqual(44)
    }
  })
}

test('a running scan survives a reload on the phone', async ({ page }) => {
  await glossarySetup(page)
  const lines = heldScan({ status: 'running' })
  await page.route('**/api/glossary/dramas/1/from-lines', lines.handler)
  await gotoTranslate(page, 'translate')
  await expect(page.getByTestId('glossary-scan-banner')).toContainText('A glossary scan is running')
  await page.reload()
  await expect(page.getByTestId('glossary-scan-banner')).toContainText('A glossary scan is running')
})
