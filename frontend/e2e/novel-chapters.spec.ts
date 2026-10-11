import { expect, test } from './fixtures'
import { listBody, mockChapters, row } from './novelChaptersMocks'

// Saved chapters panel (Source stage, novel titles): list, preview, marks.
// The chapter routes are mocked; the page itself is the real built app.

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

test('lists the saved chapters with counts, source and date, and marks what is in the translation text', async ({ page }) => {
  const rows = [row(1, { in_translation: true }), row(2, { in_translation: true }), row(3)]
  await mockChapters(page, listBody(rows, { translation_chars: 9000 }))
  await page.goto('/#/drama/2/source')
  const panel = page.getByRole('region', { name: 'Saved chapters' })
  await expect(panel.getByTestId('chapters-headline')).toHaveText('3 chapters, 12,006 characters')
  await expect(panel.getByTestId('chapters-translation')).toHaveText(
    'Translation text has 2 of 3 listed chapters (9,000 characters).',
  )
  const list = panel.getByRole('list', { name: 'Saved chapters' })
  await expect(list.getByRole('listitem')).toHaveCount(3)
  await expect(list.getByRole('listitem').first()).toContainText('xbanxia · 2026-10-08')
  await expect(list.getByRole('listitem').first()).toContainText('In translation text')
  await expect(list.getByRole('listitem').nth(2)).toContainText('Not in translation text')
})

test('shows the empty state', async ({ page }) => {
  await mockChapters(page, listBody([]))
  await page.goto('/#/drama/2/source')
  await expect(page.getByTestId('chapters-headline')).toHaveText(
    'No chapters saved yet. Import from Sources or paste text.',
  )
  // The one action that fills the panel, one tap away.
  const panel = page.getByRole('region', { name: 'Saved chapters' })
  await expect(panel.getByRole('link', { name: 'Import from Sources' })).toHaveAttribute('href', '#/sources')
  await expect(panel.getByRole('button', { name: 'Refresh' })).toHaveClass(/btn-ghost/)
})

test('shows text with no chapter markers as one Unsplit text block', async ({ page }) => {
  const block = row(1, { title: 'Unsplit text', unsplit: true, source: '', imported_at: '', chars: 90000 })
  await mockChapters(page, listBody([block], { split: false, total: 1 }))
  await page.goto('/#/drama/2/source')
  const panel = page.getByRole('region', { name: 'Saved chapters' })
  await expect(panel.getByTestId('chapters-headline')).toHaveText('Unsplit text, 90,000 characters')
  await expect(panel.getByRole('button', { name: /Unsplit text/ })).toBeVisible()
})

test('opens a preview, loads the rest with Show all, and copies', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  const rows = [row(1, { chars: 120000 })]
  const slices = await mockChapters(page, listBody(rows), (number, offset) =>
    offset === 0
      ? { drama_id: 2, number, title: '第1章 标题', source: 'xbanxia', imported_at: '', unsplit: false, chars: 120000,
          in_translation: false, offset, text: 'A'.repeat(20000), next_offset: 20000 }
      : { drama_id: 2, number, title: '第1章 标题', source: 'xbanxia', imported_at: '', unsplit: false, chars: 120000,
          in_translation: false, offset, text: 'B'.repeat(100000), next_offset: null },
  )
  await page.goto('/#/drama/2/source')
  await page.getByRole('button', { name: /第1章 标题/ }).click()
  const preview = page.getByRole('region', { name: 'Chapter preview' })
  await expect(preview).toContainText('20,000 of 120,000 characters')
  await preview.getByRole('button', { name: 'Copy' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Copied the 20,000 characters shown' })).toBeVisible()
  await preview.getByRole('button', { name: 'Show all' }).click()
  await expect(preview).toContainText('120,000 characters')
  await expect(preview.getByRole('button', { name: 'Show all' })).toHaveCount(0)
  expect(slices[0]).toEqual({ number: 1, offset: 0, limit: 20000 })
  expect(slices.every((s) => s.limit <= 50000)).toBe(true)
  await preview.getByRole('button', { name: 'Copy' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Copied.' })).toBeVisible()
})

test('is not shown for a title that is not a novel', async ({ page }) => {
  await mockChapters(page, listBody([row(1)]))
  await page.goto('/#/drama/1/source')
  await expect(page.getByRole('region', { name: 'Saved chapters' })).toHaveCount(0)
})

test('the chapter selector steps through chapters and each chapter links to its source page', async ({ page }) => {
  const rows = [row(1, { url: 'https://novel.example/b/1.html' }), row(2), row(3, { url: 'javascript:alert(1)' })]
  const slices = await mockChapters(page, listBody(rows), (number) => ({
    drama_id: 2, number, title: `第${number}章 标题`, source: 'xbanxia', imported_at: '', unsplit: false, chars: 4000 + number,
    in_translation: false, url: number === 1 ? 'https://novel.example/b/1.html' : '', offset: 0, text: `正文${number}`, next_offset: null,
  }))
  await page.goto('/#/drama/2/source')
  const panel = page.getByRole('region', { name: 'Saved chapters' })
  const nav = panel.getByRole('group', { name: 'Chapter selector' })
  await expect(nav.getByRole('button', { name: 'Previous chapter' })).toBeDisabled()
  await nav.getByLabel('Chapter', { exact: true }).selectOption({ label: '1. 第1章 标题' })
  const preview = panel.getByRole('region', { name: 'Chapter preview' })
  await expect(preview.getByTestId('chapters-text')).toHaveText('正文1')
  const link = preview.getByRole('link', { name: 'Open chapter page' })
  await expect(link).toHaveAttribute('href', 'https://novel.example/b/1.html')
  await expect(link).toHaveAttribute('rel', 'noopener noreferrer')
  await nav.getByRole('button', { name: 'Next chapter' }).click()
  await expect(preview.getByTestId('chapters-text')).toHaveText('正文2')
  await expect(preview.getByRole('link', { name: 'Open chapter page' })).toHaveCount(0)
  await nav.getByRole('button', { name: 'Next chapter' }).click()
  await expect(nav.getByRole('button', { name: 'Next chapter' })).toBeDisabled()
  await nav.getByRole('button', { name: 'Previous chapter' }).click()
  await expect(preview.getByTestId('chapters-text')).toHaveText('正文2')
  expect(slices.map((s) => s.number)).toEqual([1, 2, 3, 2])
  // Only the row with a real http(s) address gets a link.
  await expect(panel.getByRole('list', { name: 'Saved chapters' }).getByRole('link', { name: 'Open chapter page' })).toHaveCount(1)
})

test('each chapter source link sits beside its row and names its chapter', async ({ page }) => {
  const rows = [row(1, { url: 'https://novel.example/b/1.html' }), row(2, { url: 'https://novel.example/b/2.html' })]
  await mockChapters(page, listBody(rows))
  await page.goto('/#/drama/2/source')
  const list = page.getByRole('region', { name: 'Saved chapters' }).getByRole('list', { name: 'Saved chapters' })
  const link = list.getByRole('link', { name: 'Open chapter page: 2. 第2章 标题' })
  await expect(link).toHaveAttribute('href', 'https://novel.example/b/2.html')
  const rowBox = (await list.getByRole('button', { name: /第2章 标题/ }).boundingBox())!
  const linkBox = (await link.boundingBox())!
  expect(linkBox.x).toBeGreaterThanOrEqual(rowBox.x + rowBox.width)
  expect(linkBox.y).toBeLessThan(rowBox.y + rowBox.height)
})
