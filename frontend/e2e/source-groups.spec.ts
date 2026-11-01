import { expect, test, type Page } from '@playwright/test'

import { mediaPanel, mediaTab } from './mediaTabs'
import { nextFrames } from './sourceHelpers'
import { openCreditsTab, openDetailsTab, openGroup } from './source-groups'

// The Media stage's Add media tab leads with the workflow for the drama's media type, and
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

const titles = (page: Page) => page.locator('.stage-source .section-title').filter({ hasText: /^(Novel text|Attach novel text \(optional\)|Import subtitle file)$/ }).allTextContents()
const isOpen = async (page: Page, title: string) =>
  (await page.locator('details.section', { has: page.locator('summary > .section-title', { hasText: new RegExp(`^${title}$`) }) }).first().getAttribute('open')) !== null

test('an audio drama adds media first, then the subtitle import (open) and the optional novel row (closed)', async ({ page }) => {
  await stubType(page, 'audio_drama', { content_mode: 'audio_drama' })
  await stubNovel(page, false)
  await page.goto('/#/drama/2/source')
  await expect(mediaPanel(page, 'add').getByRole('radiogroup', { name: 'Get audio or video' })).toBeVisible()
  await expect(mediaTab(page, 'transcribe')).toBeVisible()
  expect(await titles(page)).toEqual(['Import subtitle file', 'Attach novel text (optional)'])
  expect(await isOpen(page, 'Import subtitle file')).toBe(true)
  expect(await isOpen(page, 'Attach novel text \\(optional\\)')).toBe(false)
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
  await expect(mediaPanel(page, 'add').getByRole('radiogroup', { name: 'Get audio or video' })).toBeVisible()
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

test('a video drama adds media first, then the subtitle import (open) and the optional novel row (closed)', async ({ page }) => {
  await stubType(page, 'video_drama')
  await page.goto('/#/drama/2/source')
  await expect(mediaPanel(page, 'add').getByRole('radiogroup', { name: 'Get audio or video' })).toBeVisible()
  expect(await titles(page)).toEqual(['Import subtitle file', 'Attach novel text (optional)'])
  expect(await isOpen(page, 'Import subtitle file')).toBe(true)
  expect(await isOpen(page, 'Attach novel text \\(optional\\)')).toBe(false)
})

test('a novel leads with Novel text, open, without raw-novel advice until a raw novel is saved', async ({ page }) => {
  await stubType(page, 'novel')
  await page.goto('/#/drama/2/source')
  const novel = page.getByRole('region', { name: 'Novel text' })
  await expect(novel.getByTestId('novel-status')).toBeVisible()
  // A novel has no Transcribe tab to add a raw novel under, so the raw-novel heading waits for one.
  await expect(novel.getByText('Raw source novel (original language, used as reference)', { exact: true })).toHaveCount(0)
  expect((await titles(page))[0]).toBe('Novel text')
  // No audio to transcribe: the Transcribe and Tools tabs are not offered.
  await expect(mediaTab(page, 'transcribe')).toHaveCount(0)
  await expect(mediaTab(page, 'tools')).toHaveCount(0)
  await page.setViewportSize({ width: 1440, height: 900 })
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/source-desktop-novel.png`, fullPage: true })
})

test('a novel with a raw novel saved labels the raw and translation text apart', async ({ page }) => {
  await stubType(page, 'novel')
  await page.route('**/api/novel/dramas/2/raw-novel', (r) => r.fulfill({ json: { drama_id: 2, present: true, size_bytes: 4096, char_count: 2000 } }))
  await page.goto('/#/drama/2/source')
  const novel = page.getByRole('region', { name: 'Novel text' })
  await expect(novel.getByText('Raw source novel (original language, used as reference)', { exact: true })).toBeVisible()
  await expect(novel.getByText('Text used for translation', { exact: true })).toBeVisible()
  await expect(novel.getByTestId('raw-status')).toContainText('Saved.')
})

test('a comic leads with image OCR open', async ({ page }) => {
  await stubType(page, 'manhwa')
  await page.goto('/#/drama/2/source')
  await expect(page.getByLabel('Page images', { exact: true })).toBeVisible()
})

test('Details and Credits hide the fields another content type owns, and Add credits reveals the form', async ({ page }) => {
  await stubType(page, 'novel', { author: '', studio: '', director: '', voice_actors: '' })
  await page.goto('/#/drama/2/source')
  await openDetailsTab(page)
  await expect(page.getByLabel('Chapter count', { exact: true })).toBeVisible()
  await expect(page.getByLabel('Content mode')).toHaveCount(0)
  await expect(page.getByLabel('Source link (where you got it)', { exact: true })).toHaveCount(1)
  await openCreditsTab(page, true)
  await expect(page.getByLabel('Director', { exact: true })).toHaveCount(0)
  await expect(page.getByLabel('Voice actors', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Romanize credits' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Add credits' }).click()
  await expect(page.getByLabel('Author', { exact: true })).toBeFocused()
})

test('"Keep original files" in Media saves through the metadata update and explains itself', async ({ page }) => {
  const drama = await (await page.request.get('/api/library/dramas/2')).json()
  let kept = false
  const sent: unknown[] = []
  await page.route('**/api/library/dramas/2', (r) => r.fulfill({ json: { ...drama, keep_original: kept } }))
  await page.route('**/api/dramas/2/metadata', (r) => {
    sent.push(r.request().postDataJSON())
    kept = true
    return r.fulfill({ json: { ...drama, keep_original: true } })
  })
  await stubNovel(page, false)
  await page.goto('/#/drama/2/source')
  // The cards above fill in as their reads land (media status, novel text) and move everything below them;
  // wait for the last of them so the click does not land on a row that has just shifted.
  await expect(page.getByLabel('Audio or video file')).toBeEnabled()
  await expect(page.getByRole('button', { name: 'Attach text' }).first()).toBeAttached()
  await nextFrames(page)
  const block = page.getByTestId('keep-original')
  const toggle = block.getByRole('switch', { name: 'Keep original files' })
  await expect(toggle).toHaveAttribute('aria-checked', 'false')
  // The click is the behaviour under test, so it goes first; the help text is checked after.
  // CI saw the click land without reaching the switch (no request sent, no pending state) on a page whose
  // event stream was being refused, so retry the click until the save starts, and never click once it has.
  await expect(async () => {
    if (sent.length === 0) await toggle.click()
    await expect(toggle).toHaveAttribute('aria-checked', 'true', { timeout: 1500 })
  }).toPass({ timeout: 15_000 })
  await block.getByRole('button', { name: 'Help: Keep original files' }).hover()
  await expect(page.getByText(/never removes this title's source media.*disk space/)).toBeVisible()
  expect(sent).toEqual([{ keep_original: true }])
})
