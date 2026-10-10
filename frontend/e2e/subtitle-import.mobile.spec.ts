import { expect, test } from '@playwright/test'

import { openGroup } from './source-groups'
import { mockSubtitleImport, SRT_FILE } from './subtitleImportMocks'

// Phone project (390x844, touch): the import panel fits, has 44px targets and imports.

test('Import subtitle file fits a phone with 44px targets', async ({ page }) => {
  const seen = await mockSubtitleImport(page)
  await page.addInitScript(() => {
    for (const k of Object.keys(localStorage)) if (k.startsWith('baihe.section.source.')) localStorage.removeItem(k)
  })
  await page.goto('/#/drama/2/source')
  await openGroup(page, 'Import subtitle file')
  const panel = page.getByTestId('subtitle-import')
  await panel.getByLabel('Subtitle file').setInputFiles(SRT_FILE)
  await expect(panel.getByTestId('subtitle-import-summary')).toBeVisible()
  await panel.scrollIntoViewIfNeeded()

  const boxes = await panel.locator('button:not(.field-help-btn, .toggle), select, .segmented label, .subtitle-import-confirm').evaluateAll((els) =>
    els.filter((e) => (e as HTMLElement).offsetParent !== null)
      .map((e) => ({ h: e.getBoundingClientRect().height, text: (e.textContent ?? '').trim().slice(0, 30) })))
  expect(boxes.length).toBeGreaterThan(2)
  for (const { h, text } of boxes) expect(h, text).toBeGreaterThanOrEqual(44)
  const { scroll, client } = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)

  await panel.getByLabel('Replace the 5 current lines (saved to history first)').check()
  await panel.getByRole('button', { name: 'Import', exact: true }).click()
  await expect(panel.getByRole('status')).toContainText('Imported 42 lines.')
  expect(seen.applies).toHaveLength(1)
})
