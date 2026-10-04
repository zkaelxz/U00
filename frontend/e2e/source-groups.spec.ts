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

const titles = (page: Page) => page.locator('.stage-source .section-title').filter({ hasText: /^(Transcribe audio or video|Novel text|Details and credits)$/ }).allTextContents()
const isOpen = async (page: Page, title: string) =>
  (await page.locator('details.section', { has: page.locator('summary > .section-title', { hasText: new RegExp(`^${title}$`) }) }).first().getAttribute('open')) !== null

test('an audio drama leads with Transcribe, open', async ({ page }) => {
  await stubType(page, 'audio_drama')
  await page.goto('/#/drama/2/source')
  await expect(page.getByRole('region', { name: 'Transcribe' })).toBeVisible()
  expect((await titles(page)).slice(0, 2)).toEqual(['Transcribe audio or video', 'Novel text'])
  expect(await isOpen(page, 'Novel text')).toBe(false)
  expect(await isOpen(page, 'Details and credits')).toBe(false)
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
