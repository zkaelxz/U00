import { expect, test } from '@playwright/test'

import { mockLive, openLive } from './liveMocks'

// Live page: the Fast captions preset and the events (skipped chunk, catching up)
// the status keeps after the message moves on. Every /api call is mocked.

test('Fast captions fills the chunk options and the start body carries them', async ({ page }) => {
  const m = await mockLive(page)
  const live = await openLive(page)
  await live.getByText('Advanced').click()
  // Today's defaults are untouched until the preset is pressed.
  await expect(live.getByLabel('Chunk', { exact: true })).toHaveValue('20')
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
