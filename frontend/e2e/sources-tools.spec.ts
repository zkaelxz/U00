import { expect as baseExpect, test } from '@playwright/test'

import { mockImports } from './sourcesImportMocks'
import { mockSources, posted } from './sourcesMocks'
import { FULL_RESOURCE, NOVEL_PREVIEW, VIDEO_PREVIEW, mockTools } from './sourcesToolsMocks'

// Sources tools, desktop: "Will this site work?" (SO02), continue from
// pasted page source after a browser check (SO03), identify media on a
// video page (SO08) and the pasted-link imports list (SO16). Every job and
// write is mocked; nothing reaches a real site.
const expect = baseExpect.configure({ timeout: 15_000 })
test.describe.configure({ timeout: 90_000 })

async function linkMode(page: import('@playwright/test').Page) {
  await page.goto('/#/sources')
  await page.getByRole('radio', { name: 'Paste a link' }).check()
}

test('site check: one fetch, verdict and what it found; nothing imported', async ({ page }) => {
  const s = await mockSources(page)
  const m = await mockImports(page, s)
  await mockTools(page, s, m)
  await linkMode(page)
  const check = page.getByRole('button', { name: 'Will this site work?' })
  await expect(check).toBeDisabled()
  await page.getByRole('textbox', { name: 'Paste a link' }).fill('https://novels.example/book/5?t=1')
  await check.click()
  await expect.poll(() => posted(s, '/api/sources/url/preflight')[0]?.body).toEqual({ url: 'https://novels.example/book/5?t=1' })
  const card = page.getByTestId('site-check')
  await expect(card.getByText('Looks importable as a novel.')).toBeVisible()
  await expect(card.getByText('novel · via static http · 5,120 characters · confidence high')).toBeVisible()
  await card.getByText('What the check found').click()
  await expect(card.getByText('Reached the page over STATIC_HTTP.')).toBeVisible()
  expect(posted(s, '/api/sources/url/preview')).toHaveLength(0)
  expect(s.unmocked).toEqual([])
})

test('browser check: paste the page source, preview it, import its text', async ({ page }) => {
  const s = await mockSources(page)
  const m = await mockImports(page, s, { previewHold: true })
  await mockTools(page, s, m)
  await linkMode(page)
  await page.getByRole('textbox', { name: 'Paste a link' }).fill('https://novels.example/book/5')
  await page.getByRole('button', { name: 'Preview' }).click()
  m.preview = 'handoff'
  const paste = page.getByRole('group', { name: 'Continue from pasted page' })
  await expect(paste).toBeVisible()
  const go = paste.getByRole('button', { name: 'Continue from pasted page' })
  await expect(go).toBeDisabled()
  const html = '<html><body><p>正文</p><script>alert(1)</script></body></html>'
  await paste.getByRole('textbox').fill(html)
  await go.click()
  await expect.poll(() => posted(s, '/api/sources/url/preview-pasted')[0]?.body).toEqual({ url: 'https://novels.example/book/5', html })

  const card = page.getByRole('article', { name: 'Link preview' })
  await expect(card.getByText('Read from the page source you pasted.')).toBeVisible()
  await expect(card.getByText(NOVEL_PREVIEW.title as string)).toBeVisible()
  // The paste is parsed on the server, never rendered here.
  await expect(page.getByText('alert(1)')).toHaveCount(0)
  await card.getByRole('combobox', { name: 'Import into' }).selectOption({ label: 'Heaven Novel' })
  await card.getByRole('button', { name: 'Import text' }).click()
  await expect.poll(() => posted(s, '/api/sources/url/import-pasted')[0]?.body).toEqual({
    url: 'https://novels.example/book/5', html, drama_id: 11,
  })
  expect(posted(s, '/api/sources/url/import')).toHaveLength(0)
  await expect(card.getByTestId('url-import-result')).toContainText('Added 5,120 characters')
  expect(s.unmocked).toEqual([])
})

test('video page: identify media, pick the main resource, download that one', async ({ page }) => {
  const s = await mockSources(page)
  const m = await mockImports(page, s, { previewBody: VIDEO_PREVIEW })
  await mockTools(page, s, m)
  await linkMode(page)
  await page.getByRole('textbox', { name: 'Paste a link' }).fill('https://video.example/watch?v=1')
  await page.getByRole('button', { name: 'Preview' }).click()
  const card = page.getByRole('article', { name: 'Link preview' })
  await card.getByRole('button', { name: 'Identify media on this page' }).click()
  await expect.poll(() => posted(s, '/api/sources/url/identify-media')[0]?.body).toEqual({ url: 'https://video.example/watch?v=1' })
  const found = card.getByTestId('media-identify')
  await expect(found.getByText('DRM detected on this page; a protected stream won\'t be decrypted.')).toBeVisible()
  await expect(found.getByText('Subtitle (zh): https://cdn.example/a.vtt')).toBeVisible()
  const radios = found.getByRole('radio')
  await expect(radios).toHaveCount(3)
  await expect(found.getByRole('radio', { name: 'The page link itself' })).toBeChecked()
  await found.getByRole('radio', { name: /Main · video · 1080p/ }).check()
  await expect.poll(() => s.calls.some((c) => c.path === '/api/sources/url/identify-media/resource?run_id=run1&index=1')).toBe(true)

  await card.getByRole('combobox', { name: 'Import into' }).selectOption({ label: 'Radio Play' })
  await card.getByRole('button', { name: 'Download' }).click()
  await expect.poll(() => posted(s, '/api/media/dramas/13/download-url')[0]?.body).toEqual({
    url: FULL_RESOURCE, audio_only: true, confirm_replace_audio: false,
  })
  expect(s.unmocked).toEqual([])
})

test('source settings: pasted-link imports list', async ({ page }) => {
  const s = await mockSources(page)
  const m = await mockImports(page, s)
  await mockTools(page, s, m)
  await page.goto('/#/sources')
  await page.locator('summary').filter({ hasText: 'Source settings' }).first().click()
  await page.locator('summary').filter({ hasText: 'Pasted-link imports' }).click()
  const list = page.getByTestId('recent-extractions')
  await expect(list.getByText('https://novels.example/book/5')).toBeVisible()
  await expect(list.getByText('Worked: Static HTTP + Deterministic extraction')).toBeVisible()
  await expect(list.getByText('novel · AI calls: 0 · confidence high')).toBeVisible()
  await list.getByText('Details').click()
  await expect(list.getByText('Authentication: NOT_REQUIRED')).toBeVisible()
  expect(s.unmocked).toEqual([])
})
