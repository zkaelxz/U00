import { expect, test } from '@playwright/test'

import { mockLive, openLive } from './liveMocks'

// Live says what it is missing before Start: the controls that need a package, a model or a GPU
// are off with the reason, and the missing pieces list under Start with their fix.

const LINK = 'https://www.youtube.com/watch?v=abc'

test('a PC without Whisper, FFmpeg or yt-dlp cannot start, and each is listed with its fix', async ({ page }) => {
  const m = await mockLive(page, { readiness: { faster_whisper: false, ffmpeg: false, yt_dlp: false } })
  const live = await openLive(page)
  await live.getByLabel('Stream link', { exact: true }).fill(LINK)
  await expect(live.getByRole('button', { name: 'Start', exact: true })).toBeDisabled()
  await expect(live.getByText('Still needed: what is listed under Start.')).toBeVisible()
  await expect(live.getByTestId('needs-whisper')).toContainText("Transcription (Whisper) isn't installed")
  await expect(live.getByTestId('needs-whisper').getByRole('button', { name: 'Install transcription' })).toBeVisible()
  await expect(live.getByTestId('needs-ffmpeg')).toContainText("FFmpeg isn't installed")
  await expect(live.getByTestId('needs-yt_dlp')).toContainText('Live needs yt-dlp')
  await expect(live.getByTestId('needs-yt_dlp').getByRole('link', { name: 'Open Diagnostics' })).toHaveAttribute('href', /package=yt-dlp/)
  await expect(live.getByText(/pip install/)).toHaveCount(0)
  expect(m.posts).toEqual([])
})

test('a PC with everything installed starts as before', async ({ page }) => {
  const m = await mockLive(page)
  const live = await openLive(page)
  await live.getByLabel('Stream link', { exact: true }).fill(LINK)
  await expect(live.getByRole('button', { name: 'Start', exact: true })).toBeEnabled()
  await expect(live.locator('[data-testid^="needs-"]')).toHaveCount(0)
  expect(m.unmocked).toEqual([])
})

test('the GPU box is off with a reason when no NVIDIA GPU is found', async ({ page }) => {
  await mockLive(page, { readiness: { gpu_found: false } })
  const live = await openLive(page)
  await live.getByText('Advanced').click()
  await expect(live.getByLabel('Use GPU for Whisper', { exact: true })).toBeDisabled()
  await expect(live.getByTestId('live-gpu-note')).toContainText('No NVIDIA GPU was found')
})

test('away from the PC, a Whisper model that is not downloaded cannot be picked; at the PC it downloads on first use', async ({ page }) => {
  await mockLive(page, { remote: true, readiness: { whisper_cached_medium: false } })
  const live = await openLive(page)
  await live.getByText('Advanced').click()
  const medium = live.getByLabel('Whisper model', { exact: true }).locator('option[value="medium"]')
  await expect(medium).toBeDisabled()
  await expect(medium).toHaveText('medium (not available)')
})

test('at the PC, a model that is not downloaded is offered and says it downloads first', async ({ page }) => {
  await mockLive(page, { readiness: { whisper_cached_medium: false } })
  const live = await openLive(page)
  await live.getByText('Advanced').click()
  const medium = live.getByLabel('Whisper model', { exact: true }).locator('option[value="medium"]')
  await expect(medium).toBeEnabled()
  await expect(medium).toHaveText('medium (downloads first)')
})
