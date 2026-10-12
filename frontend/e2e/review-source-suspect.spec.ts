import { expect, test, type Page } from '@playwright/test'

// The "Source looks off" chip in the Review toolbar opens the flagged lines; "Merge with
// next" posts the ordinary merge for that line and the one after it. Lines and writes are
// mocked, so the shared library is never touched.

const row = (id: number, zh: string, flagged: boolean) => ({
  id, idx: id - 1, start: id * 2, end: id * 2 + 2, zh, en: '', speaker: null, speaker_manual: false, sfx: false,
  flag: flagged ? 'source_suspect' : null,
  flag_note: flagged ? 'ends mid-phrase; the next line is only “身”' : null, dub_filename: null, lang: null,
})

async function open(page: Page, merges: string[]) {
  let merged = false
  const lines = () => (merged
    ? [row(1, '你好。', false), row(2, '他不甘願的做起身。', false)]
    : [row(1, '你好。', false), row(2, '他不甘願的做起', true), row(3, '身。', false)])
  await page.route('**/api/**', (route) => (route.request().method() === 'GET' ? route.continue() : route.abort()))
  await page.route('**/api/review/dramas/3/lines?*', (route) => {
    const l = lines()
    return route.fulfill({ json: { lines: l, page: 1, page_size: 40, total: l.length, flagged_count: merged ? 0 : 1, untranslated_count: l.length } })
  })
  await page.route('**/api/source-suspect/dramas/3', (route) =>
    route.fulfill({ json: merged ? { count: 0, items: [] } : {
      count: 1, items: [{ line_id: 2, idx: 1, zh: '他不甘願的做起', note: 'ends mid-phrase; the next line is only “身”', next_line_id: 3, next_zh: '身。' }],
    } }))
  await page.route('**/api/source-suspect/dramas/3/check', (route) => route.fulfill({ json: { flagged_count: 0, cleared_count: 1 } }))
  await page.route('**/api/restructure/dramas/3/merge', (route) => {
    merges.push(route.request().postData() ?? '')
    merged = true
    return route.fulfill({ json: { line_ids: [1, 2], lines: [row(2, '他不甘願的做起身。', false)] } })
  })
  await page.goto('/#/drama/3/review')
  await expect(page.locator('.review-line:not(.review-skeleton)')).not.toHaveCount(0)
}

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

for (const [name, size] of [['desktop', { width: 1280, height: 900 }], ['phone', { width: 375, height: 800 }]] as const) {
  test(`${name}: the chip opens the list and Merge with next uses the merge route`, async ({ page }) => {
    await page.setViewportSize(size)
    const merges: string[] = []
    await open(page, merges)
    const chip = page.locator('.review-suspect-chip')
    await expect(chip).toContainText('1')
    await chip.click()
    const item = page.getByTestId('suspect-item')
    await expect(item).toContainText('他不甘願的做起')
    await expect(item).toContainText('ends mid-phrase')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await item.getByRole('button', { name: /Merge with next/ }).click()
    await expect(chip).toHaveCount(0)
    expect(merges).toHaveLength(1)
    expect(JSON.parse(merges[0]).line_ids).toEqual([2, 3])
  })
}

test('no chip when no line is flagged', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await open(page, [])
  await page.unroute('**/api/source-suspect/dramas/3')
  await page.route('**/api/source-suspect/dramas/3', (route) => route.fulfill({ json: { count: 0, items: [] } }))
  await page.reload()
  await expect(page.locator('.review-line:not(.review-skeleton)')).not.toHaveCount(0)
  await expect(page.locator('.review-suspect-chip')).toHaveCount(0)
})
