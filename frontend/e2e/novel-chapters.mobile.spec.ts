import { expect, test } from '@playwright/test'
import { listBody, mockChapters, row } from './novelChaptersMocks'

// Phone project: the saved-chapters list and preview fit 390px, keep 44px
// targets and never scroll the page sideways.

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

test('chapters list and preview fit the phone', async ({ page }) => {
  const rows = Array.from({ length: 12 }, (_, i) => row(i + 1, { in_translation: i < 4, title: `第${i + 1}章 一个相当长的章节标题用来测试换行 ${'x'.repeat(30)}` }))
  await mockChapters(page, listBody(rows, { translation_chars: 5000 }), (number) => ({
    drama_id: 2, number, title: '第1章', source: 'xbanxia', imported_at: '', unsplit: false, chars: 3000,
    in_translation: false, offset: 0, text: '正文。'.repeat(1000), next_offset: null,
  }))
  await page.goto('/#/drama/2/source')
  const panel = page.getByRole('region', { name: 'Saved chapters' })
  await expect(panel.getByTestId('chapters-headline')).toContainText('12 chapters')
  const rowsLoc = panel.locator('.chapters-row')
  for (let i = 0; i < 3; i++) {
    const box = await rowsLoc.nth(i).boundingBox()
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(44)
  }
  await rowsLoc.first().click()
  const preview = page.getByRole('region', { name: 'Chapter preview' })
  await expect(preview.getByTestId('chapters-text')).toBeVisible()
  for (const name of ['Copy', 'Close']) {
    const box = await preview.getByRole('button', { name }).boundingBox()
    expect(box?.height ?? 0, name).toBeGreaterThanOrEqual(44)
  }
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
})
