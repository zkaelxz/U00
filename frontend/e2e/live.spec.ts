import { expect, test } from '@playwright/test'

import { SCREENS, SID, cue, mockLive, openLive } from './liveMocks'

// Live page (#/live): start a session from a pasted link, watch lines
// arrive by polling, stop it. Every /api call is mocked (liveMocks.ts).

test('start, see lines arrive, stop', async ({ page }) => {
  const m = await mockLive(page)
  const live = await openLive(page)
  await expect(page.getByRole('link', { name: 'Live' })).toHaveAttribute('aria-current', 'page')

  const start = live.getByRole('button', { name: 'Start', exact: true })
  await expect(start).toBeDisabled()
  await expect(live.getByText('Still needed: a stream link.')).toBeVisible()
  await live.getByLabel('Stream link', { exact: true }).fill('youtube.com/live')
  await expect(live.getByText(/full link starting with https/)).toBeVisible()
  await live.getByLabel('Stream link', { exact: true }).fill('https://www.youtube.com/watch?v=abc')
  await live.getByLabel('Language', { exact: true }).selectOption('ja')
  // Only engines with a key are offered; the first is picked.
  await expect(live.getByLabel('AI engine', { exact: true }).locator('option')).toHaveText(['DeepSeek (paid)', 'Fake'])

  await live.getByText('Advanced').click()
  await live.getByLabel('Chunk', { exact: true }).fill('30')
  await live.getByLabel('Stop after', { exact: true }).fill('15')
  await live.getByLabel('Use GPU for Whisper', { exact: true }).check()
  await start.click()

  await expect.poll(() => m.posts.length).toBe(1)
  expect(m.posts[0].body).toEqual({
    url: 'https://www.youtube.com/watch?v=abc', source_language: 'ja', whisper_size: 'small', segment_seconds: 30,
    overlap_seconds: 3, engine: 'deepseek', max_minutes: 15, use_gpu: true,
  })
  expect(m.posts[0].headers['x-baihe-local']).toBe('1')

  // Queued: the one action is Cancel, and the form is locked.
  await expect(live.getByTestId('live-status')).toHaveText('Waiting for the GPU')
  await expect(live.getByRole('button', { name: 'Cancel', exact: true })).toBeVisible()
  await expect(live.getByLabel('Stream link', { exact: true })).toBeDisabled()

  m.state.status = 'running'
  m.state.message = 'Listening'
  await expect(live.getByText('Waiting for the first chunk…')).toBeVisible()
  m.state.cues = [cue(0), cue(1)]
  await expect(live.getByTestId('live-status')).toHaveText('Listening · 2 lines')
  m.state.cues = [cue(0), cue(1), cue(2)]
  const items = live.getByRole('list', { name: 'Live lines, newest first' }).getByRole('listitem')
  await expect(items).toHaveCount(3)
  await expect(items.first()).toContainText('0:40')
  await expect(items.first()).toContainText('Line 2')
  // Polls ask only for what's new.
  expect(m.polls).toContain('after=2')
  await page.screenshot({ path: `${SCREENS}/desktop-running.png`, fullPage: true })

  await live.getByRole('button', { name: 'Stop', exact: true }).click()
  await expect(live.getByTestId('live-status')).toHaveText('Stopped · 3 lines')
  expect(m.posts[1].url).toContain(`/api/live/sessions/${SID}/stop`)
  await expect(items).toHaveCount(3)
  await expect(live.getByRole('button', { name: 'Start', exact: true })).toBeEnabled()
  expect(m.unmocked).toEqual([])
})

test('reopening the page shows the running session', async ({ page }) => {
  const m = await mockLive(page)
  m.state.sessions = [{ session_id: SID, status: 'running', engine: 'deepseek', cue_count: 1 }]
  m.state.status = 'running'
  m.state.message = 'Listening'
  m.state.cues = [cue(0)]
  const live = await openLive(page)
  await expect(live.getByTestId('live-status')).toHaveText('Listening · 1 line')
  await expect(live.getByRole('button', { name: 'Stop', exact: true })).toBeVisible()
  expect(m.unmocked).toEqual([])
})

test('a refused start is explained', async ({ page }) => {
  const m = await mockLive(page, { remote: true })
  m.state.startStatus = 403
  const live = await openLive(page)
  await expect(live.getByText(/owner to allow importing from links/)).toBeVisible()
  await live.getByLabel('Stream link', { exact: true }).fill('https://www.youtube.com/watch?v=abc')
  await live.getByRole('button', { name: 'Start', exact: true }).click()
  await expect(live.getByRole('alert')).toContainText('import from a link')
  expect(m.unmocked).toEqual([])
})

test('Start stays disabled until the engine list has loaded', async ({ page }) => {
  await mockLive(page)
  let release: () => void = () => {}
  const held = new Promise<void>((r) => { release = r })
  // Hold the engines answer: a filled-in link must not enable Start yet.
  await page.route('**/api/translate/engines', async (route) => {
    await held
    await route.fallback()
  })
  const live = await openLive(page)
  await live.getByLabel('Stream link', { exact: true }).fill('https://www.youtube.com/watch?v=abc')
  const start = live.getByRole('button', { name: 'Start', exact: true })
  await expect(live.getByText('Loading engines…')).toBeVisible()
  await expect(start).toBeDisabled()
  release()
  await expect(start).toBeEnabled()
})
