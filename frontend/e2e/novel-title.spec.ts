import { expect, test } from './fixtures'
import { listBody, mockChapters, row } from './novelChaptersMocks'

// The novel title page (#/drama/2, no stage): chapters and the five actions,
// without the video stepper. Stage routes are covered by their own specs.

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

const progress = (lineCount: number) => ({
  drama_id: 2, stage_index: 1, stage: 'translate', line_count: lineCount, untranslated_count: lineCount,
  flagged_count: 0, has_audio: false, has_dub_track: false, exported: false, stages: [],
})

test('a novel opens on its chapter list with Translate, Add chapters, Glossary, Export and Read, and no stage tabs', async ({ page }) => {
  await mockChapters(page, listBody([row(1, { in_translation: true }), row(2)], { translation_chars: 4001 }))
  await page.route('**/api/workflow/dramas/2/progress', (route) => route.fulfill({ json: progress(12) }))
  await page.goto('/#/drama/2')
  await expect(page.getByTestId('drama-title')).toBeVisible()
  await expect(page.getByRole('navigation', { name: 'Stages' })).toHaveCount(0)
  await expect(page.getByTestId('chapters-headline')).toHaveText('2 chapters, 8,003 characters')
  const list = page.getByRole('list', { name: 'Saved chapters' })
  await expect(list.getByRole('listitem')).toHaveCount(2)
  await expect(list.getByRole('listitem').first()).toContainText('In translation text')
  await expect(page.getByRole('link', { name: 'Translate', exact: true })).toHaveAttribute('href', '#/drama/2/translate?tab=translate')
  await expect(page.getByRole('link', { name: 'Review', exact: true })).toHaveCount(0)
  await expect(page.getByRole('link', { name: 'Add chapters' })).toHaveAttribute('href', '#/drama/2/source')
  await expect(page.getByRole('link', { name: 'Glossary' })).toHaveAttribute('href', '#/drama/2/translate?tab=glossary')
  await expect(page.getByRole('link', { name: 'Export' })).toHaveAttribute('href', '#/drama/2/export')
  await expect(page.getByRole('link', { name: 'Read' })).toHaveAttribute('href', '#/read/2')
})

test('with lines, each chapter row shows how far it is translated and a Read link to its first page', async ({ page }) => {
  await mockChapters(page, listBody([row(1, { in_translation: true }), row(2)], { translation_chars: 4001 }))
  await page.route('**/api/workflow/dramas/2/progress', (route) => route.fulfill({ json: progress(80) }))
  await page.route('**/api/novel/dramas/2/line-chapters', (route) => route.fulfill({ json: {
    drama_id: 2, total_lines: 80, source: 'saved',
    chapters: [
      { number: 1, title: 'One', first_idx: 0, line_count: 40, translated: 40, reviewed: 0 },
      { number: 2, title: 'Two', first_idx: 40, line_count: 40, translated: 12, reviewed: 0 },
    ],
  } }))
  await page.goto('/#/drama/2')
  const items = page.getByRole('list', { name: 'Saved chapters' }).getByRole('listitem')
  await expect(items.first()).toContainText('Translated')
  await expect(items.nth(1)).toContainText('12 of 40 lines translated')
  await expect(items.nth(1).getByRole('link', { name: /^Read chapter 2/ })).toHaveAttribute('href', '#/read/2?page=2')
})

test('Translate again previews one chapter, counts hand edits, and runs only after confirming', async ({ page }) => {
  const posted: unknown[] = []
  const est = (usd: number) => ({ estimated_usd: usd, target_line_count: 40, engine: 'fake', model: null, free: false,
    cap_applies: false, effective_cap_usd: null, estimate_above_cap: false, estimate_is_lower_bound: false,
    monthly_refusal: false, thinking: false })
  await mockChapters(page, listBody([row(1), row(2)]))
  await page.route('**/api/workflow/dramas/2/progress', (route) => route.fulfill({ json: progress(80) }))
  await page.route('**/api/novel/dramas/2/line-chapters', (route) => route.fulfill({ json: {
    drama_id: 2, total_lines: 80, source: 'saved',
    chapters: [
      { number: 1, title: 'One', first_idx: 0, line_count: 40, translated: 40, reviewed: 0 },
      { number: 2, title: 'Two', first_idx: 40, line_count: 40, translated: 0, reviewed: 0 },
    ] } }))
  await page.route('**/api/translate-run/dramas/2/chapters/1/retranslate', (route) => route.fulfill({ json: {
    drama_id: 2, number: 1, line_count: 40, hand_edited_count: 3, estimate: est(0.5), estimate_with_hand_edited: est(0.6) } }))
  await page.route('**/api/translate-run/dramas/2/chapters/1/retranslate/run', (route) => {
    posted.push(route.request().postDataJSON())
    return route.fulfill({ json: { job_id: 'translate_2', drama_id: 2, engine: 'fake', target_line_count: 37,
      line_ids: [1], skipped_hand_edited_count: 3 } })
  })
  await page.goto('/#/drama/2')
  const items = page.getByRole('list', { name: 'Saved chapters' }).getByRole('listitem')
  await expect(items.nth(1).getByRole('button', { name: /Translate chapter 2 again/ })).toHaveCount(0)
  await items.first().getByRole('button', { name: /Translate chapter 1 again/ }).click()
  const form = page.getByRole('form', { name: 'Translate chapter 1 again' })
  await expect(form).toContainText('37 of its 40 lines')
  await expect(form).toContainText('$0.50')
  expect(posted).toEqual([])
  await form.getByRole('checkbox').check()
  await expect(form).toContainText('40 of its 40 lines')
  await form.getByRole('button', { name: 'Translate again' }).click()
  await expect(form).toHaveCount(0)
  expect(posted).toEqual([{ include_hand_edited: true }])
})

test('Translate is a button (it runs the link chain) while the novel has chapters but no lines, and Read still opens the Reader', async ({ page }) => {
  await mockChapters(page, listBody([row(1)]))
  await page.route('**/api/workflow/dramas/2/progress', (route) => route.fulfill({ json: progress(0) }))
  await page.goto('/#/drama/2')
  await expect(page.getByRole('button', { name: 'Translate', exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Read' })).toHaveAttribute('href', '#/read/2')
})

test('a novel with no chapters says so', async ({ page }) => {
  await mockChapters(page, listBody([]))
  await page.goto('/#/drama/2')
  await expect(page.getByTestId('chapters-headline')).toHaveText('No chapters saved yet. Import from Sources or paste text.')
})

test('Show more chapters loads the next page of a long novel, not one bigger page', async ({ page }) => {
  const all = Array.from({ length: 250 }, (_, i) => row(i + 1))
  const offsets: number[] = []
  await page.route('**/api/novel/dramas/2/raw-novel/chapters?*', (route) => {
    const url = new URL(route.request().url())
    const offset = Number(url.searchParams.get('offset'))
    const limit = Number(url.searchParams.get('limit'))
    offsets.push(offset)
    return route.fulfill({ json: listBody(all.slice(offset, offset + limit), { total: all.length, offset, limit }) })
  })
  await page.goto('/#/drama/2')
  const list = page.getByRole('list', { name: 'Saved chapters' })
  await expect(list.getByRole('listitem')).toHaveCount(100)
  await page.getByRole('button', { name: 'Show more chapters' }).click()
  await expect(list.getByRole('listitem')).toHaveCount(200)
  await page.getByRole('button', { name: 'Show more chapters' }).click()
  await expect(list.getByRole('listitem')).toHaveCount(250)
  expect(offsets).toEqual([0, 100, 200])
})

test('Review leads once every line has English', async ({ page }) => {
  await mockChapters(page, listBody([row(1)]))
  await page.route('**/api/workflow/dramas/2/progress', (route) => route.fulfill({ json: { ...progress(12), untranslated_count: 0 } }))
  await page.goto('/#/drama/2')
  await expect(page.getByRole('link', { name: 'Review', exact: true })).toHaveAttribute('href', '#/drama/2/review')
})
