import { expect, test } from '@playwright/test'

import { installHitArea } from './hitArea'
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

test('a 130-character chapter title with no spaces wraps at 360px and the row links keep 44px', async ({ page }) => {
  await installHitArea(page)
  await page.setViewportSize({ width: 360, height: 780 })
  for (const long of ['x'.repeat(130), '章'.repeat(130)]) {
    const rows = [row(1, { title: long, url: 'https://novel.example/b/1.html' }), row(2)]
    await mockChapters(page, listBody(rows), (number) => ({
      drama_id: 2, number, title: long, source: 'xbanxia', imported_at: '', unsplit: false, chars: 3000,
      in_translation: false, url: 'https://novel.example/b/1.html', offset: 0, text: '正文', next_offset: null,
    }))
    await page.goto('/#/drama/2/source')
    const panel = page.getByRole('region', { name: 'Saved chapters' })
    await panel.locator('.chapters-row').first().click()
    const preview = panel.getByRole('region', { name: 'Chapter preview' })
    await expect(preview.getByRole('heading')).toBeVisible()
    const heading = (await preview.getByRole('heading').boundingBox())!
    expect(heading.x + heading.width).toBeLessThanOrEqual(360)
    const link = panel.getByRole('list', { name: 'Saved chapters' }).getByRole('link').first()
    expect(await link.evaluate((n) => window.hitHeight(n))).toBeGreaterThanOrEqual(44)
    const { scroll, client } = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth,
    }))
    expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
    await page.unrouteAll({ behavior: 'ignoreErrors' })
  }
})
