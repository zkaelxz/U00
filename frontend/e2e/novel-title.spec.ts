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
  await expect(page.getByRole('link', { name: 'Translate', exact: true })).toHaveAttribute('href', '#/drama/2/translate')
  await expect(page.getByRole('link', { name: 'Add chapters' })).toHaveAttribute('href', '#/drama/2/source')
  await expect(page.getByRole('link', { name: 'Glossary' })).toHaveAttribute('href', '#/drama/2/translate?tab=glossary')
  await expect(page.getByRole('link', { name: 'Export' })).toHaveAttribute('href', '#/drama/2/export')
  await expect(page.getByRole('link', { name: 'Read' })).toHaveAttribute('href', '#/read/2')
})

test('Translate opens Media while the novel has no lines yet, and Read still opens the Reader', async ({ page }) => {
  await mockChapters(page, listBody([row(1)]))
  await page.route('**/api/workflow/dramas/2/progress', (route) => route.fulfill({ json: progress(0) }))
  await page.goto('/#/drama/2')
  await expect(page.getByRole('link', { name: 'Translate', exact: true })).toHaveAttribute('href', '#/drama/2/source')
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
