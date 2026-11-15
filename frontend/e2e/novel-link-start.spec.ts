import type { Page } from '@playwright/test'

import { expect, test } from './fixtures'
import { listBody, row } from './novelChaptersMocks'

// The link box on a novel title with no lines (#/drama/2) puts a run on the PC queue that imports, makes the
// text and lines and finds names, then stops at the glossary review. The fake server answers the run's reads as
// the queue would: a press starts it, and the next read finds it at the review (or stopped, for a failing run).

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

type Run = { state: string; step: string | null; reason: string | null }
interface Server { chapters: number; text: boolean; lines: number; run: Run; posts: { url: string }[] }

async function mockServer(page: Page, start: Partial<Server> = {}, end: Run = { state: 'review', step: null, reason: null }): Promise<Server> {
  const s: Server = { chapters: 0, text: false, lines: 0, run: { state: 'idle', step: null, reason: null }, posts: [], ...start }
  await page.route('**/api/novel/dramas/2/raw-novel/chapters?*', (r) =>
    r.fulfill({ json: listBody(s.chapters ? [row(1)] : [], { total: s.chapters }) }))
  await page.route('**/api/novel/dramas/2/status', (r) =>
    r.fulfill({ json: { drama_id: 2, has_novel_text: s.text, char_count: 10, chapters: s.chapters, ocr_running: false } }))
  await page.route('**/api/workflow/dramas/2/progress', (r) => r.fulfill({ json: {
    drama_id: 2, stage_index: 1, stage: 'translate', line_count: s.lines, untranslated_count: s.lines,
    flagged_count: 0, has_audio: false, has_dub_track: false, exported: false, stages: [] } }))
  await page.route('**/api/queue/novel-flow/2', (r) => {
    if (r.request().method() === 'POST') {
      s.posts.push(r.request().postDataJSON() as { url: string })
      const first = { state: 'running', step: s.chapters ? 'lines' : 'import', reason: null }
      const answer = s.run.state === 'running' ? s.run : first
      s.run = end
      return r.fulfill({ json: answer })
    }
    return r.fulfill({ json: s.run })
  })
  return s
}

test('the link starts the run, which stops at the glossary review', async ({ page }) => {
  const s = await mockServer(page)
  await page.goto('/#/drama/2')
  await page.getByLabel('Link to the novel').fill('https://example.com/book/1')
  await page.getByRole('button', { name: 'Import from link' }).click()
  const review = page.getByRole('link', { name: 'Review glossary and translate' })
  await expect(review).toHaveAttribute('href', '#/drama/2/translate?tab=translate')
  expect(s.posts).toEqual([{ url: 'https://example.com/book/1' }])
})

test('a run that stopped says why and offers Continue', async ({ page }) => {
  await mockServer(page, {}, { state: 'stopped', step: 'import', reason: 'Skipped: the page has no novel text.' })
  await page.goto('/#/drama/2')
  await page.getByLabel('Link to the novel').fill('https://example.com/book/1')
  await page.getByRole('button', { name: 'Import from link' }).click()
  await expect(page.getByRole('status')).toHaveText('Stopped: Skipped: the page has no novel text.')
  await expect(page.getByRole('button', { name: 'Continue' })).toBeEnabled()
  await expect(page.getByRole('link', { name: 'Review glossary and translate' })).toHaveCount(0)
})

test('a refused start shows its error and offers Continue', async ({ page }) => {
  await mockServer(page)
  await page.route('**/api/queue/novel-flow/2', (r) => r.request().method() === 'POST'
    ? r.fulfill({ status: 403, json: { detail: 'Preparing the imported chapters as the text works only at the PC.' } })
    : r.fulfill({ json: { state: 'idle', step: null, reason: null } }))
  await page.goto('/#/drama/2')
  await page.getByLabel('Link to the novel').fill('https://example.com/book/1')
  await page.getByRole('button', { name: 'Import from link' }).click()
  await expect(page.getByRole('alert')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Continue' })).toBeEnabled()
})

test('opening the title mid-run shows the run, not a button to start it again', async ({ page }) => {
  await mockServer(page, { chapters: 3, text: true, run: { state: 'running', step: 'names', reason: null } })
  await page.goto('/#/drama/2')
  await expect(page.getByRole('status')).toHaveText('Finding names for the glossary')
  await expect(page.getByRole('button', { name: 'Continue' })).toBeDisabled()
})

test('with chapters saved, Continue starts the run without a link', async ({ page }) => {
  const s = await mockServer(page, { chapters: 3, text: true })
  await page.goto('/#/drama/2')
  await expect(page.getByLabel('Link to the novel')).toHaveCount(0)
  await page.getByRole('button', { name: 'Continue' }).click()
  await expect(page.getByRole('link', { name: 'Review glossary and translate' })).toBeVisible()
  expect(s.posts).toEqual([{ url: '' }])
})

test('Translate on a novel with chapters and no lines starts the run to the glossary review', async ({ page }) => {
  const s = await mockServer(page, { chapters: 3, text: true })
  await page.goto('/#/drama/2')
  await expect(page.getByRole('link', { name: 'Translate', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'Translate', exact: true }).click()
  await expect(page.getByRole('link', { name: 'Review glossary and translate' })).toBeVisible()
  expect(s.posts).toHaveLength(1)
})

test('Enter in the link box starts the run', async ({ page }) => {
  const s = await mockServer(page)
  await page.goto('/#/drama/2')
  await page.getByLabel('Link to the novel').fill('https://example.com/book/1')
  await page.getByLabel('Link to the novel').press('Enter')
  await expect(page.getByRole('link', { name: 'Review glossary and translate' })).toBeVisible()
  expect(s.posts[0]).toEqual({ url: 'https://example.com/book/1' })
})

test('Import from link stays disabled until a link is typed', async ({ page }) => {
  await mockServer(page)
  await page.goto('/#/drama/2')
  await expect(page.getByRole('button', { name: 'Import from link' })).toBeDisabled()
})

test('a title that already has translated lines shows no link box', async ({ page }) => {
  await mockServer(page, { chapters: 3, text: true, lines: 40 })
  await page.route('**/api/workflow/dramas/2/progress', (r) => r.fulfill({ json: {
    drama_id: 2, stage_index: 2, stage: 'review', line_count: 40, untranslated_count: 0,
    flagged_count: 0, has_audio: false, has_dub_track: false, exported: false, stages: [] } }))
  await page.goto('/#/drama/2')
  await expect(page.getByTestId('drama-title')).toBeVisible()
  await expect(page.getByRole('form', { name: 'Import from a link' })).toHaveCount(0)
})
