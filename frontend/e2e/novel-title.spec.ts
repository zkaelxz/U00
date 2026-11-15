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

test('once translation has started the link box becomes Add chapters, which never starts a translation', async ({ page }) => {
  const calls: string[] = []
  await mockChapters(page, listBody([row(1)]))
  await page.route('**/api/workflow/dramas/2/progress', (route) => route.fulfill({ json: { ...progress(80), untranslated_count: 30 } }))
  await page.route('**/api/sources/import/**', (route) => route.fulfill({ status: 404, json: {} }))
  await page.route('**/api/novel/dramas/2/add-chapters-from-sources', (route) => {
    calls.push('add')
    return route.fulfill({ json: { char_count: 90, added_chars: 0 } })
  })
  await page.route('**/api/translate-run/**', (route) => { calls.push('translate'); return route.fulfill({ json: {} }) })
  await page.goto('/#/drama/2')
  const box = page.getByRole('form', { name: 'Add chapters from a link' })
  await expect(box).toBeVisible()
  await expect(page.getByRole('form', { name: 'Import from a link' })).toHaveCount(0)
  await expect(box.getByRole('button', { name: 'Add from link' })).toBeDisabled()
  await box.getByLabel('Link to the next chapter').fill('https://example.com/ch2')
  await expect(box.getByRole('button', { name: 'Add from link' })).toBeEnabled()
  expect(calls).not.toContain('translate')
})

test('the cast card lists the people the scan found and applies only the ticked ones', async ({ page }) => {
  const posted: unknown[] = []
  await mockChapters(page, listBody([row(1)]))
  await page.route('**/api/workflow/dramas/2/progress', (route) => route.fulfill({ json: progress(10) }))
  await page.route('**/api/glossary/dramas/2/from-novel', (route) => route.fulfill({ json: {
    job_id: 'g', status: 'done', progress: 1, message: '', result: { proposals: [] }, run_id: 'r' } }))
  await page.route('**/api/characters/dramas/2/cast-proposal', (route) => route.fulfill({ json: {
    drama_id: 2, ready: true, has_series: true, people: [
      { term: '林晚', name: 'Lin Wan', pronouns: 'she/her', who: 'the heroine', occurrences: 9, current_pronouns: '', exists: false },
      { term: '沈一', name: 'Shen Yi', pronouns: '', who: 'her husband', occurrences: 7, current_pronouns: '', exists: false },
      { term: '老人', name: 'Old Man', pronouns: 'he/him', who: '', occurrences: 2, current_pronouns: 'he/him', exists: true },
    ] } }))
  await page.route('**/api/characters/dramas/2/cast-proposal/apply', (route) => {
    posted.push(route.request().postDataJSON())
    return route.fulfill({ json: { set: [], added: ['Lin Wan', 'Shen Yi'], kept: [], not_applied: [] } })
  })
  await page.goto('/#/drama/2')
  const card = page.getByRole('region', { name: 'Cast proposal' })
  await expect(card).toContainText('the heroine')
  await expect(card.getByLabel('Include Lin Wan')).toBeChecked()
  await expect(card.getByLabel('Include Shen Yi')).not.toBeChecked()
  await expect(card.getByLabel('Include Old Man')).toBeDisabled()
  await expect(card).toContainText('Already set: he/him')
  await card.getByLabel('Pronouns for Shen Yi').selectOption('he/him')
  expect(posted).toEqual([])
  await card.getByRole('button', { name: 'Apply to characters' }).click()
  await expect(card).toContainText('Set pronouns for Lin Wan, Shen Yi.')
  expect(posted).toEqual([{ people: [{ name: 'Lin Wan', pronouns: 'she/her' }, { name: 'Shen Yi', pronouns: 'he/him' }] }])
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
