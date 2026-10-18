import type { Page } from '@playwright/test'

import { expect, test } from './fixtures'

// Source-stage URL download, Narration chunk-and-tag, Export > Send to Jellyfin and the Reader say what is
// missing before the click. Readiness, the media status and the narration config are faked; no job starts.

const READY = { ffmpeg: true, yt_dlp: true, faster_whisper: true }

async function readiness(page: Page, patch: Record<string, boolean>) {
  await page.route('**/api/setup/readiness', (r) => r.fulfill({ json: { ...READY, ...patch } }))
}

async function openUrlDownload(page: Page) {
  await page.route('**/api/media/dramas/1/status', (r) =>
    r.fulfill({ json: { drama_id: 1, has_audio: false, has_source_video: false, reads_burned_in_subtitles: false, upload_max_mb: 2048 } }),
  )
  await page.goto('/#/drama/1/source')
  await page.getByRole('radio', { name: 'From a URL' }).check()
  await page.getByRole('textbox', { name: 'Video or audio link' }).fill('https://www.youtube.com/watch?v=abc')
}

test('URL download is off with a yt-dlp fix for a video page, but not for a file link', async ({ page }) => {
  await readiness(page, { yt_dlp: false })
  await openUrlDownload(page)
  const go = page.getByRole('button', { name: 'Download', exact: true })
  await expect(go).toBeDisabled()
  await expect(page.getByTestId('url-download').getByText(/needs yt-dlp/)).toBeVisible()
  await page.getByRole('textbox', { name: 'Video or audio link' }).fill('https://host.example/media/x.mp4')
  await expect(go).toBeEnabled()
})

test('URL download is off when FFmpeg is missing, for any link', async ({ page }) => {
  await readiness(page, { ffmpeg: false })
  await openUrlDownload(page)
  await page.getByRole('textbox', { name: 'Video or audio link' }).fill('https://host.example/media/x.mp3')
  await expect(page.getByRole('button', { name: 'Download', exact: true })).toBeDisabled()
  await expect(page.getByTestId('url-download').getByText(/FFmpeg isn't installed/)).toBeVisible()
})

test('URL download stays on when everything is installed', async ({ page }) => {
  await readiness(page, {})
  await openUrlDownload(page)
  await expect(page.getByRole('button', { name: 'Download', exact: true })).toBeEnabled()
})

async function openNarration(page: Page, engines: Array<{ key: string; key_configured: boolean }>) {
  await page.route('**/api/library/dramas/1', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const real = await route.fetch()
    await route.fulfill({ response: real, json: { ...(await real.json()), media_type: 'novel', content_mode: 'novel_narration' } })
  })
  await page.route('**/api/narration/dramas/1/config', (r) =>
    r.fulfill({
      json: {
        drama_id: 1, is_narration: true, has_novel_source: true, engines, default_engine: engines[0].key,
        max_chunk_chars: 120, existing_line_count: 0, replaces_existing_lines: false, job_running: false,
      },
    }),
  )
  await page.goto('/#/drama/1/source')
  await page.getByText('Chunk and tag speakers').click()
}

test('Narration Chunk and tag is off, with the fix, when the engine has no key', async ({ page }) => {
  await readiness(page, {})
  await openNarration(page, [{ key: 'claude', key_configured: false }])
  await expect(page.getByRole('button', { name: 'Chunk and tag', exact: true })).toBeDisabled()
  await expect(page.getByTestId('engine-gate')).toContainText('no API key')
})

test('Narration Chunk and tag stays on for an engine with a key', async ({ page }) => {
  await readiness(page, {})
  await openNarration(page, [{ key: 'claude', key_configured: true }])
  await expect(page.getByRole('button', { name: 'Chunk and tag', exact: true })).toBeEnabled()
  await expect(page.getByTestId('engine-gate')).toHaveCount(0)
})

test('Send to Jellyfin says when the video to copy is missing', async ({ page }) => {
  await readiness(page, {})
  await page.route('**/api/jellyfin/config', (r) =>
    r.fulfill({ json: { enabled: true, server_url: 'http://192.168.1.20:8096', library_dir: 'D:\\Media', key_configured: true } }),
  )
  await page.route('**/api/media/dramas/1/status', (r) =>
    r.fulfill({ json: { drama_id: 1, has_audio: true, has_source_video: false, reads_burned_in_subtitles: false, upload_max_mb: 2048 } }),
  )
  await page.goto('/#/drama/1/export')
  const card = page.getByRole('region', { name: 'Send to Jellyfin' })
  await card.getByText('Put the subtitles in your Jellyfin library').click()
  await card.getByLabel('Video', { exact: true }).selectOption('source')
  await expect(card.getByTestId('jellyfin-media-reason')).toContainText('No source video yet')
  await expect(card.getByRole('button', { name: 'Send to Jellyfin' })).toBeDisabled()
  await card.getByLabel('Video', { exact: true }).selectOption('none')
  await expect(card.getByRole('button', { name: 'Send to Jellyfin' })).toBeEnabled()
})
