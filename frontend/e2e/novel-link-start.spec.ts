import type { Page } from '@playwright/test'

import { expect, test } from './fixtures'
import { listBody, row } from './novelChaptersMocks'

// The link box on a novel title with no lines (#/drama/2): import, attach, tag, glossary scan, then a stop at the
// glossary review. The fake server changes state only when a step's call arrives, as the real jobs do.

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

interface Server { chapters: number; text: boolean; lines: number; glossary: string; calls: string[] }

async function mockServer(page: Page, start: Partial<Server> = {}, failAt?: string): Promise<Server> {
  const s: Server = { chapters: 0, text: false, lines: 0, glossary: 'idle', calls: [], ...start }
  const done = (jobId: string) => ({ job_id: jobId, status: 'done', progress: 1, message: '', error: null, description: null, gpu_touching: false, started_at: 1, finished_at: 2, updated_at: 2 })
  const step = (name: string, route: import('@playwright/test').Route, effect: () => void, body: unknown) => {
    s.calls.push(name)
    if (name === failAt) return route.fulfill({ status: 422, json: { detail: `${name} refused` } })
    effect()
    return route.fulfill({ json: body })
  }
  await page.route('**/api/novel/dramas/2/raw-novel/chapters?*', (r) =>
    r.fulfill({ json: listBody(s.chapters ? [row(1)] : [], { total: s.chapters }) }))
  await page.route('**/api/novel/dramas/2/status', (r) =>
    r.fulfill({ json: { drama_id: 2, has_novel_text: s.text, char_count: 10, chapters: s.chapters, ocr_running: false } }))
  await page.route('**/api/workflow/dramas/2/progress', (r) => r.fulfill({ json: {
    drama_id: 2, stage_index: 1, stage: 'translate', line_count: s.lines, untranslated_count: s.lines,
    flagged_count: 0, has_audio: false, has_dub_track: false, exported: false, stages: [] } }))
  await page.route('**/api/glossary/dramas/2/from-novel', (r) => r.request().method() === 'POST'
    ? step('extract', r, () => { s.glossary = 'done' }, { job_id: 'g', engine: 'x', paired: false })
    : r.fulfill({ json: { job_id: 'g', status: s.glossary, progress: null, message: '', proposals: null, run_id: null } }))
  await page.route('**/api/sources/url/import', (r) => step('import', r, () => { s.chapters = 1 }, { job_id: 'sourceimport_2' }))
  await page.route('**/api/jobs/**', (r) => r.fulfill({ json: done('x') }))
  await page.route('**/api/novel/dramas/2/attach-from-sources', (r) => step('attach', r, () => { s.text = true }, { char_count: 10 }))
  await page.route('**/api/narration/dramas/2/config', (r) => r.fulfill({ json: {
    drama_id: 2, is_narration: true, has_novel_source: true, engines: [{ key: 'gemini', key_configured: true }],
    default_engine: 'gemini', max_chunk_chars: 1000, existing_line_count: 0, replaces_existing_lines: false, job_running: false } }))
  await page.route('**/api/narration/dramas/2/run*', (r) => step('tag', r, () => { s.lines = 40 }, { job_id: 'narration_2' }))
  return s
}

test('the link runs every step and stops at the glossary review', async ({ page }) => {
  const s = await mockServer(page)
  await page.goto('/#/drama/2')
  await page.getByLabel('Link to the novel').fill('https://example.com/book/1')
  await page.getByRole('button', { name: 'Import from link' }).click()
  const review = page.getByRole('link', { name: 'Review the glossary' })
  await expect(review).toHaveAttribute('href', '#/drama/2/translate?tab=glossary')
  expect(s.calls).toEqual(['import', 'attach', 'tag', 'extract'])
})

for (const failing of ['import', 'attach', 'tag', 'extract']) {
  test(`a refused ${failing} step shows its error, stops there and offers Continue`, async ({ page }) => {
    const s = await mockServer(page, {}, failing)
    await page.goto('/#/drama/2')
    await page.getByLabel('Link to the novel').fill('https://example.com/book/1')
    await page.getByRole('button', { name: 'Import from link' }).click()
    await expect(page.getByRole('alert')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Continue' })).toBeEnabled()
    expect(s.calls.at(-1)).toBe(failing)
    await expect(page.getByRole('link', { name: 'Review the glossary' })).toHaveCount(0)
  })
}

test('a reload mid-chain continues from the chapters the server already holds', async ({ page }) => {
  const s = await mockServer(page, { chapters: 3, text: true })
  await page.goto('/#/drama/2')
  await expect(page.getByLabel('Link to the novel')).toHaveCount(0)
  await page.getByRole('button', { name: 'Continue' }).click()
  await expect(page.getByRole('link', { name: 'Review the glossary' })).toBeVisible()
  expect(s.calls).toEqual(['tag', 'extract'])
})

test('Enter in the link box starts the chain', async ({ page }) => {
  const s = await mockServer(page)
  await page.goto('/#/drama/2')
  await page.getByLabel('Link to the novel').fill('https://example.com/book/1')
  await page.getByLabel('Link to the novel').press('Enter')
  await expect(page.getByRole('link', { name: 'Review the glossary' })).toBeVisible()
  expect(s.calls[0]).toBe('import')
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
