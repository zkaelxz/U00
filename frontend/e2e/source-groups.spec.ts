import { expect, test, type Page } from '@playwright/test'

import { openGroup } from './source-groups'

// The Source stage leads with the workflow for the drama's media type, and
// the details form shows only the fields that type uses. The drama read is
// the seeded drama 2 served with a patched media type.

async function stubType(page: Page, media_type: string, patch: Record<string, unknown> = {}) {
  const drama = await (await page.request.get('/api/library/dramas/2')).json()
  await page.route('**/api/library/dramas/2', (r) => r.fulfill({ json: { ...drama, media_type, ...patch } }))
  // Remembered open state must not leak between types.
  await page.addInitScript(() => {
    for (const k of Object.keys(localStorage)) if (k.startsWith('baihe.section.source.')) localStorage.removeItem(k)
  })
}

// The seeded library is shared by every spec, so say outright whether drama 2 has a novel.
async function stubNovel(page: Page, has: boolean) {
  await page.route('**/api/novel/dramas/2/status', (r) => r.fulfill({ json: { drama_id: 2, has_novel_text: has, char_count: has ? 1200 : 0, chapters: has ? 3 : 0, ocr_running: false } }))
  await page.route('**/api/novel/dramas/2/raw-novel', (r) => r.fulfill({ json: { drama_id: 2, present: false, size_bytes: 0, char_count: 0 } }))
}

const titles = (page: Page) => page.locator('.stage-source .section-title').filter({ hasText: /^(Transcribe audio or video|Novel text|Attach novel text \(optional\)|Details and credits)$/ }).allTextContents()
const isOpen = async (page: Page, title: string) =>
  (await page.locator('details.section', { has: page.locator('summary > .section-title', { hasText: new RegExp(`^${title}$`) }) }).first().getAttribute('open')) !== null

test('an audio drama leads with Transcribe, open', async ({ page }) => {
  await stubType(page, 'audio_drama', { content_mode: 'audio_drama' })
  await stubNovel(page, false)
  await page.goto('/#/drama/2/source')
  await expect(page.getByRole('region', { name: 'Transcribe' })).toBeVisible()
  expect((await titles(page)).slice(0, 2)).toEqual(['Transcribe audio or video', 'Attach novel text (optional)'])
  expect(await isOpen(page, 'Attach novel text \\(optional\\)')).toBe(false)
  expect(await isOpen(page, 'Details and credits')).toBe(false)
})

test('an audio drama with no novel gets one optional row that opens to the attach controls', async ({ page }) => {
  await stubType(page, 'audio_drama', { content_mode: 'audio_drama' })
  await stubNovel(page, false)
  await page.goto('/#/drama/2/source')
  await expect(page.getByText('Raw source novel', { exact: false })).toHaveCount(0)
  const row = page.getByRole('region', { name: 'Attach novel text (optional)' })
  await expect(row.getByRole('button', { name: 'Attach text' })).toBeHidden()
  await openGroup(page, 'Attach novel text \\(optional\\)')
  await expect(row.getByRole('button', { name: 'Attach text' })).toBeVisible()
  await expect(row.getByRole('region', { name: 'Raw source novel (original language, used as reference)' })).toBeVisible()
})

test('a streamer VOD with no novel shows neither novel section', async ({ page }) => {
  await stubType(page, 'streamer_vod', { content_mode: 'streamer_vod' })
  await stubNovel(page, false)
  await page.goto('/#/drama/2/source')
  await expect(page.getByRole('region', { name: 'Transcribe' })).toBeVisible()
  await expect(page.locator('.stage-source .section-title', { hasText: /novel/i })).toHaveCount(0)
  await expect(page.getByText('Raw source novel', { exact: false })).toHaveCount(0)
})

test('a streamer VOD that already has novel text still shows Novel text', async ({ page }) => {
  await stubType(page, 'streamer_vod', { content_mode: 'streamer_vod' })
  await stubNovel(page, true)
  await page.goto('/#/drama/2/source')
  const novel = page.getByRole('region', { name: 'Novel text' })
  await expect(novel).toBeVisible()
  await openGroup(page, 'Novel text')
  await expect(novel.getByTestId('novel-status')).toHaveText('Attached: 1,200 characters, 3 chapters.')
})

test('a novel leads with Novel text, open, with raw and translation text labelled apart', async ({ page }) => {
  await stubType(page, 'novel')
  await page.goto('/#/drama/2/source')
  const novel = page.getByRole('region', { name: 'Novel text' })
  await expect(novel.getByText('Text used for translation', { exact: true })).toBeVisible()
  await expect(novel.getByText('Raw source novel (original language, used as reference)', { exact: true })).toBeVisible()
  expect((await titles(page))[0]).toBe('Novel text')
  expect(await isOpen(page, 'Transcribe audio or video')).toBe(false)
  await page.setViewportSize({ width: 1440, height: 900 })
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/source-desktop-novel.png`, fullPage: true })
})

test('a comic leads with image OCR open', async ({ page }) => {
  await stubType(page, 'manhwa')
  await page.goto('/#/drama/2/source')
  await expect(page.getByLabel('Page images', { exact: true })).toBeVisible()
})

test('Details hide the fields another content type owns, and Add credits reveals the form', async ({ page }) => {
  await stubType(page, 'novel', { author: '', studio: '', director: '', voice_actors: '' })
  await page.goto('/#/drama/2/source')
  await openGroup(page, 'Details and credits')
  await page.locator('.section-title', { hasText: 'Edit details' }).click()
  await expect(page.getByLabel('Chapter count', { exact: true })).toBeVisible()
  await expect(page.getByLabel('Director', { exact: true })).toHaveCount(0)
  await expect(page.getByLabel('Voice actors', { exact: true })).toHaveCount(0)
  await expect(page.getByLabel('Content mode')).toHaveCount(0)
  await expect(page.getByLabel('Source URL', { exact: true })).toHaveCount(1)
  await expect(page.getByRole('button', { name: 'Romanize credits' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Add credits' }).click()
  await expect(page.getByLabel('Author', { exact: true })).toBeFocused()
})
