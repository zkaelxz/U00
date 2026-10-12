import type { Page } from '@playwright/test'

import { expect, test } from './fixtures'
import { gotoMedia, mediaPanel } from './mediaTabs'
import { enableDeveloperMode, openAdvanced } from './sourceHelpers'

// Settings that do not apply to a title's media type are not in the page at all.
// Seeded titles: 1 audio drama, 2 novel, 3 video drama; the comic is title 1 with its type replaced.

const tabNames = (page: Page) => page.getByRole('tablist', { name: 'Media sections' }).getByRole('tab').allTextContents()

async function openAsComic(page: Page) {
  await page.route('**/api/library/dramas/1', async (route) => {
    const res = await route.fetch()
    await route.fulfill({ response: res, json: { ...(await res.json()), media_type: 'manhua' } })
  })
  await page.goto('/#/drama/1/source')
  await expect(page.getByRole('tablist', { name: 'Media sections' })).toBeVisible()
}

test('audio drama: speech settings are there, hardsub ones are not', async ({ page }) => {
  await enableDeveloperMode(page)
  await page.goto('/#/drama/1/source')
  const advanced = await openAdvanced(page)
  for (const name of ['Min silence', 'VAD threshold', 'Beam size', 'Separate vocals first', 'Separation backend']) {
    await expect(advanced.getByText(name, { exact: true }).first()).toBeVisible()
  }
  await expect(advanced.getByText('Hardsub interval', { exact: true })).toHaveCount(0)
  await expect(advanced.getByText('Hardsub OCR', { exact: true })).toHaveCount(0)
})

test('video drama: speech and hardsub settings are all there', async ({ page }) => {
  await enableDeveloperMode(page)
  await page.goto('/#/drama/3/source')
  const advanced = await openAdvanced(page)
  for (const name of ['Min silence', 'Separate vocals first', 'Hardsub interval', 'Hardsub OCR']) {
    await expect(advanced.getByText(name, { exact: true }).first()).toBeVisible()
  }
})

test('novel: leads with translating the text, and has no transcription or audio-tuning settings', async ({ page }) => {
  await enableDeveloperMode(page)
  await page.goto('/#/drama/2/source')
  await expect(page.getByRole('tablist', { name: 'Media sections' })).toBeVisible()
  expect(await tabNames(page)).toEqual(['Add media', 'Details', 'Credits'])
  const panel = mediaPanel(page, 'add')
  await expect(panel.getByRole('region', { name: 'Translate the novel' })).toBeVisible()
  await expect(panel.getByRole('region', { name: 'Novel text' })).toBeVisible()
  await expect(panel.getByRole('radiogroup', { name: 'Where the lines come from' })).toHaveCount(0)
  await expect(panel.locator('.section-title', { hasText: /^Import subtitle file$/ })).toHaveCount(0)
  await expect(panel.getByRole('region', { name: 'Advanced options' })).toHaveCount(0)
  for (const name of ['Min silence', 'Separate vocals first', 'VAD threshold', 'Beam size']) {
    await expect(page.getByText(name, { exact: true })).toHaveCount(0)
  }
  // Audio comes after translating: the upload waits behind a fold.
  await expect(panel.locator('.section-title', { hasText: /^Audio \(optional, for dubbing later\)$/ })).toBeVisible()
  // The text is the first thing on the page, ahead of the audio.
  const order = await panel.locator('.section-title, h3.source-subhead').allTextContents()
  expect(order.findIndex((t) => /Translate the novel/.test(t))).toBeLessThan(order.findIndex((t) => /Audio \(optional/.test(t)))
})

test('a novel not yet in narration mode offers one button that sets it up for translating', async ({ page }) => {
  await page.goto('/#/drama/2/source')
  const flow = mediaPanel(page, 'add').getByRole('region', { name: 'Translate the novel' })
  await expect(flow.getByTestId('novel-steps')).toBeVisible()
  await expect(flow.getByRole('button', { name: 'Set up this title for translating text' })).toBeVisible()
  await expect(flow.getByRole('link', { name: 'Translate the lines' })).toHaveAttribute('href', /#\/drama\/2\/translate$/)
})

test('comic: only the page-image text settings remain', async ({ page }) => {
  await enableDeveloperMode(page)
  await openAsComic(page)
  expect(await tabNames(page)).toEqual(['Add media', 'Details', 'Credits'])
  const panel = mediaPanel(page, 'add')
  await expect(panel.getByText('Chapter images (OCR)')).toBeVisible()
  await expect(panel.getByRole('radiogroup', { name: 'Where the lines come from' })).toHaveCount(0)
  await expect(panel.getByRole('radiogroup', { name: 'Get audio or video' })).toHaveCount(0)
  await expect(panel.locator('.section-title', { hasText: /^Import subtitle file$/ })).toHaveCount(0)
  await expect(panel.getByRole('region', { name: 'Translate the novel' })).toHaveCount(0)
  await expect(page.getByText('Min silence', { exact: true })).toHaveCount(0)
})

test('the transcript choice without audio does not dead-end: it offers to translate the text as a novel', async ({ page }) => {
  // Answered here so the shared seeded title is not changed.
  const calls: string[] = []
  await page.route('**/api/novel/dramas/1/attach-text', (r) => { calls.push(`text:${r.request().postDataJSON().text}`); return r.fulfill({ json: { char_count: 6 } }) })
  await page.route('**/api/source/dramas/1/config', (r) => {
    if (r.request().method() !== 'POST') return r.fallback()
    calls.push(`mode:${r.request().postDataJSON().content_mode}`)
    return r.fulfill({ json: {} })
  })
  await page.route('**/api/dramas/1/metadata', (r) => { calls.push(`type:${r.request().postDataJSON().media_type}`); return r.fulfill({ json: {} }) })
  await gotoMedia(page, 'transcribe', 1)
  await expect(page.getByTestId('settings-summary')).toBeVisible()
  const run = mediaPanel(page, 'transcribe')
  // The seeded title has no audio, and a transcript is lined up with audio.
  const asNovel = run.getByRole('button', { name: 'Translate this text as a novel' })
  await expect(asNovel).toBeDisabled()
  await run.getByLabel('Transcript text').fill('第一章 开始')
  await expect(asNovel).toBeEnabled()
  await asNovel.click()
  await expect.poll(() => calls).toEqual(['text:第一章 开始', 'mode:novel_narration', 'type:novel'])
})
