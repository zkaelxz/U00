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
    overlap_seconds: 3, engine: 'deepseek', model: null, max_minutes: 15, use_gpu: true,
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

test('the status names the stage, flags a long wait on Ollama, and says what a stop is waiting on', async ({ page }) => {
  const m = await mockLive(page)
  const live = await openLive(page)
  await live.getByLabel('Stream link', { exact: true }).fill('https://www.youtube.com/watch?v=abc')
  await live.getByRole('button', { name: 'Start', exact: true }).click()
  await expect(live.getByTestId('live-status')).toHaveText('Waiting for the GPU')

  m.state.status = 'running'
  m.state.message = 'Chunk 1: transcribing with Whisper small (CPU)'
  await expect(live.getByTestId('live-status')).toHaveText('Chunk 1: transcribing with Whisper small (CPU) · 0 lines')
  m.state.message = 'Chunk 1: translating with qwen3:8b (Ollama) Still waiting on Ollama after 75 s: it may be loading the model.'
  await expect(live.getByTestId('live-status')).toContainText('Still waiting on Ollama after 75 s')

  // A stop that can't act at once says why, instead of "finishes the current step first".
  await page.route(`**/api/live/sessions/${SID}/stop`, (route) => {
    m.state.message = 'Cancelling... Whisper is still transcribing chunk 1 and cannot be interrupted mid-chunk; it stops when that finishes (there is no time estimate yet).'
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ session_id: SID, stopping: true }) })
  })
  await live.getByRole('button', { name: 'Stop', exact: true }).click()
  await expect(live.getByRole('button', { name: 'Stopping…' })).toBeDisabled()
  await expect(live.getByTestId('live-status')).toContainText('cannot be interrupted mid-chunk')
  expect(m.unmocked).toEqual([])
})

test('model picker: shown for an engine with a model list, choice is sent and shown in status', async ({ page }) => {
  const m = await mockLive(page, { ollama: true })
  const live = await openLive(page)
  await live.getByLabel('Stream link', { exact: true }).fill('https://www.youtube.com/watch?v=abc')
  await live.getByLabel('AI engine', { exact: true }).selectOption('ollama')
  const model = live.getByLabel('Model', { exact: true })
  await expect(model.locator('option')).toHaveText(['Engine default', 'qwen3:8b', 'gemma4:12b'])
  await expect(model).toHaveValue('')
  // No model list for this engine: no picker.
  await live.getByLabel('AI engine', { exact: true }).selectOption('fake')
  await expect(model).toHaveCount(0)
  await live.getByLabel('AI engine', { exact: true }).selectOption('ollama')
  await model.selectOption('gemma4:12b')
  await live.getByRole('button', { name: 'Start', exact: true }).click()
  await expect.poll(() => m.posts.length).toBe(1)
  expect(m.posts[0].body).toMatchObject({ engine: 'ollama', model: 'gemma4:12b' })
  m.state.status = 'running'
  m.state.message = 'Listening'
  m.state.model = 'gemma4:12b'
  await expect(live.getByTestId('live-status')).toHaveText('Listening · 0 lines · gemma4:12b')
  await expect(model).toBeDisabled()
  expect(m.unmocked).toEqual([])
})

test('model picker: a remembered model the engine no longer offers falls back to the default', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('baihe.pref.live.options', JSON.stringify({ engine: 'ollama', model: 'gone:1b' })))
  const m = await mockLive(page, { ollama: true })
  const live = await openLive(page)
  await live.getByLabel('Stream link', { exact: true }).fill('https://www.youtube.com/watch?v=abc')
  await expect(live.getByLabel('Model', { exact: true })).toHaveValue('')
  await expect(live.getByTestId('live-model-note')).toContainText("default model will be used")
  await live.getByRole('button', { name: 'Start', exact: true }).click()
  await expect.poll(() => m.posts.length).toBe(1)
  expect(m.posts[0].body).toMatchObject({ engine: 'ollama', model: null })
})

test('model picker: when the engine list fails to load, says the default model is used', async ({ page }) => {
  const m = await mockLive(page, { enginesFail: true })
  const live = await openLive(page)
  await expect(live.getByTestId('live-model-note')).toHaveText("Couldn't load the model list; the engine's default model will be used.")
  await expect(live.getByLabel('Model', { exact: true })).toHaveCount(0)
  await expect(live.getByRole('button', { name: 'Start', exact: true })).toBeDisabled()
  expect(m.posts).toEqual([])
})
