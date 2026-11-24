import { expect, test } from '@playwright/test'

import { mockLive, openLive } from './liveMocks'

// Live page: the Fast captions preset and the events (skipped chunk, catching up)
// the status keeps after the message moves on. Every /api call is mocked.

test('Fast captions fills the chunk options and the start body carries them', async ({ page }) => {
  const m = await mockLive(page)
  const live = await openLive(page)
  await live.getByText('Advanced').click()
  // Today's defaults are untouched until the preset is pressed.
  await expect(live.getByLabel('Chunk', { exact: true })).toHaveValue('10')
  await expect(live.getByLabel('Overlap', { exact: true })).toHaveValue('3')

  await live.getByTestId('live-fast-captions').click()
  await expect(live.getByLabel('Chunk', { exact: true })).toHaveValue('4')
  await expect(live.getByLabel('Overlap', { exact: true })).toHaveValue('1')
  await expect(live.getByLabel('Whisper model', { exact: true })).toHaveValue('small')

  await live.getByLabel('Stream link', { exact: true }).fill('https://www.youtube.com/watch?v=abc')
  await live.getByRole('button', { name: 'Start', exact: true }).click()
  await expect.poll(() => m.posts.length).toBe(1)
  expect(m.posts[0].body).toMatchObject({
    segment_seconds: 4, overlap_seconds: 1, whisper_size: 'small', reply_without_thinking: true,
  })
})

test('Balanced captions fills large-v3 with an 8 s chunk', async ({ page }) => {
  const m = await mockLive(page)
  const live = await openLive(page)
  await live.getByText('Advanced').click()
  await live.getByTestId('live-balanced-captions').click()
  await expect(live.getByLabel('Chunk', { exact: true })).toHaveValue('8')
  await expect(live.getByLabel('Overlap', { exact: true })).toHaveValue('3')
  await expect(live.getByLabel('Whisper model', { exact: true })).toHaveValue('large-v3')
  await live.getByLabel('Stream link', { exact: true }).fill('https://www.youtube.com/watch?v=abc')
  await live.getByRole('button', { name: 'Start', exact: true }).click()
  await expect.poll(() => m.posts.length).toBe(1)
  expect(m.posts[0].body).toMatchObject({ segment_seconds: 8, overlap_seconds: 3, whisper_size: 'large-v3' })
})

test('Fast and Balanced captions use the model downloaded for the picked language', async ({ page }) => {
  await mockLive(page, { liveOptions: { language_whisper_defaults: { zh: 'breeze-asr-25', ja: 'ja-1.5b' } } })
  const live = await openLive(page)
  await live.getByText('Advanced').click()
  await live.getByLabel('Language', { exact: true }).selectOption('zh')
  await live.getByTestId('live-fast-captions').click()
  await expect(live.getByLabel('Whisper model', { exact: true })).toHaveValue('breeze-asr-25')
  await expect(live.getByLabel('Chunk', { exact: true })).toHaveValue('4')
  await live.getByLabel('Language', { exact: true }).selectOption('ja')
  await live.getByTestId('live-balanced-captions').click()
  await expect(live.getByLabel('Whisper model', { exact: true })).toHaveValue('ja-1.5b')
  await live.getByLabel('Language', { exact: true }).selectOption('ko')
  await live.getByTestId('live-balanced-captions').click()
  await expect(live.getByLabel('Whisper model', { exact: true })).toHaveValue('large-v3')
  // The model follows the language after a preset, until one is picked by hand.
  await live.getByLabel('Language', { exact: true }).selectOption('zh')
  await expect(live.getByLabel('Whisper model', { exact: true })).toHaveValue('breeze-asr-25')
  await live.getByLabel('Whisper model', { exact: true }).selectOption('large-v3')
  await live.getByLabel('Language', { exact: true }).selectOption('ja')
  await expect(live.getByLabel('Whisper model', { exact: true })).toHaveValue('large-v3')
})

test('skipped chunks and catching up stay on screen', async ({ page }) => {
  const m = await mockLive(page)
  const live = await openLive(page)
  await live.getByLabel('Stream link', { exact: true }).fill('https://www.youtube.com/watch?v=abc')
  await live.getByRole('button', { name: 'Start', exact: true }).click()
  await expect.poll(() => m.posts.length).toBe(1)
  m.state.status = 'running'
  m.state.message = 'Capturing audio: waiting for chunk 4 (10 s of stream each)'
  m.state.notes = ['Skipped chunk 3 (Whisper did not finish in 60 s).', 'Skipped 20 s to catch up.']
  const notes = live.getByTestId('live-notes')
  await expect(notes).toContainText('Skipped chunk 3 (Whisper did not finish in 60 s).')
  await expect(notes).toContainText('Skipped 20 s to catch up.')
})
