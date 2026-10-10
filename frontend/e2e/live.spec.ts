import { expect, test } from '@playwright/test'

import { SCREENS, SID, cue, expectSwitchKeepsItsSize, mockLive, openLive, pendingCue } from './liveMocks'

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
    reply_without_thinking: true,
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

test('a 403 while polling stops the polling and says why', async ({ page }) => {
  const m = await mockLive(page)
  m.state.sessions = [{ session_id: SID, status: 'running', engine: 'deepseek', cue_count: 0 }]
  let reads = 0
  await page.route(`**/api/live/sessions/${SID}?*`, (route) => {
    reads += 1
    return route.fulfill({ status: 403, json: { error: { code: 'forbidden', message: 'Not allowed.' } } })
  })
  const live = await openLive(page)
  await expect(live.getByRole('alert')).toContainText('owner')
  // The event stream failing over to polling re-reads once; after that, no timer.
  await page.waitForTimeout(1000)
  const seen = reads
  await page.waitForTimeout(5000)
  expect(reads).toBe(seen)
})

test('repeated read failures are surfaced, and clear when the feed returns', async ({ page }) => {
  const m = await mockLive(page)
  m.state.sessions = [{ session_id: SID, status: 'running', engine: 'deepseek', cue_count: 0 }]
  let fail = true
  await page.route(`**/api/live/sessions/${SID}?*`, (route) => fail
    ? route.fulfill({ status: 500, json: { error: { code: 'internal', message: 'Boom.' } } })
    : route.fulfill({ json: { session_id: SID, status: 'running', message: 'Listening', model: null, progress: 0, notes: [], cues: [], next_index: 0 } }))
  const live = await openLive(page)
  await expect(live.getByRole('alert')).toBeVisible({ timeout: 20_000 })
  fail = false
  await expect(live.getByRole('alert')).toHaveCount(0, { timeout: 10_000 })
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

test('model picker: Ollama and gemma4:12b are the labelled default, and every other choice still works', async ({ page }) => {
  const m = await mockLive(page, { ollama: true })
  const live = await openLive(page)
  await live.getByLabel('Stream link', { exact: true }).fill('https://www.youtube.com/watch?v=abc')
  const engine = live.getByLabel('AI engine', { exact: true })
  await expect(engine).toHaveValue('ollama')
  const model = live.getByLabel('Model', { exact: true })
  await expect(model.locator('option')).toHaveText(['qwen3:8b', 'gemma4:12b (default)'])
  await expect(model).toHaveValue('gemma4:12b')
  await expect.poll(() => m.ollamaChecks).toEqual(['gemma4:12b'])
  await expect(live.getByTestId('live-ollama-note')).toHaveCount(0)
  // Another engine without a model list: no picker, and the thinking switch is still offered for DeepSeek.
  await engine.selectOption('fake')
  await expect(model).toHaveCount(0)
  await engine.selectOption('ollama')
  await model.selectOption('qwen3:8b')
  await live.getByRole('button', { name: 'Start', exact: true }).click()
  await expect.poll(() => m.posts.length).toBe(1)
  expect(m.posts[0].body).toMatchObject({ engine: 'ollama', model: 'qwen3:8b' })
  m.state.status = 'running'
  m.state.message = 'Listening'
  m.state.model = 'qwen3:8b'
  await expect(live.getByTestId('live-status')).toHaveText('Listening · 0 lines · qwen3:8b')
  await expect(model).toBeDisabled()
  expect(m.unmocked).toEqual([])
})

test('default start sends Ollama with gemma4:12b and replies without thinking', async ({ page }) => {
  const m = await mockLive(page, { ollama: true })
  const live = await openLive(page)
  await live.getByLabel('Stream link', { exact: true }).fill('https://www.youtube.com/watch?v=abc')
  await live.getByRole('button', { name: 'Start', exact: true }).click()
  await expect.poll(() => m.posts.length).toBe(1)
  expect(m.posts[0].body).toMatchObject({ engine: 'ollama', model: 'gemma4:12b', reply_without_thinking: true })
})

test('Ollama missing: says so plainly, keeps the engine, and another engine can be picked', async ({ page }) => {
  const m = await mockLive(page, { ollama: true, ollamaMissing: true })
  const live = await openLive(page)
  const note = live.getByTestId('live-ollama-note')
  await expect(note).toContainText('Ollama doesn\'t have the model gemma4:12b')
  await expect(note).toContainText('Pick another engine above')
  await expect(live.getByLabel('AI engine', { exact: true })).toHaveValue('ollama')
  await live.getByLabel('AI engine', { exact: true }).selectOption('deepseek')
  await expect(note).toHaveCount(0)
  await live.getByLabel('Stream link', { exact: true }).fill('https://www.youtube.com/watch?v=abc')
  await live.getByRole('button', { name: 'Start', exact: true }).click()
  await expect.poll(() => m.posts.length).toBe(1)
  expect(m.posts[0].body).toMatchObject({ engine: 'deepseek' })
})

test('Reply without thinking: on by default, can be turned off, disabled with a note where unsupported', async ({ page }) => {
  const m = await mockLive(page, { ollama: true })
  const live = await openLive(page)
  await live.getByText('Advanced').click()
  const toggle = live.getByRole('switch', { name: 'Reply without thinking' })
  await expect(toggle).toHaveAttribute('aria-checked', 'true')
  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-checked', 'false')
  await toggle.click()
  await live.getByLabel('AI engine', { exact: true }).selectOption('fake')
  await expect(toggle).toBeDisabled()
  await expect(live.getByTestId('live-thinking-note')).toContainText("can't switch thinking off")
  await live.getByLabel('AI engine', { exact: true }).selectOption('deepseek')
  await expect(toggle).toBeEnabled()
  await toggle.click()
  await live.getByLabel('Stream link', { exact: true }).fill('https://www.youtube.com/watch?v=abc')
  await live.getByRole('button', { name: 'Start', exact: true }).click()
  await expect.poll(() => m.posts.length).toBe(1)
  expect(m.posts[0].body).toMatchObject({ engine: 'deepseek', reply_without_thinking: false })
})

test('model picker: a remembered model the engine no longer offers falls back to the default', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('baihe.pref.live.options', JSON.stringify({ engine: 'ollama', model: 'gone:1b' })))
  const m = await mockLive(page, { ollama: true })
  const live = await openLive(page)
  await live.getByLabel('Stream link', { exact: true }).fill('https://www.youtube.com/watch?v=abc')
  await expect(live.getByLabel('Model', { exact: true })).toHaveValue('gemma4:12b')
  await expect(live.getByTestId('live-model-note')).toContainText("default model will be used")
  await live.getByRole('button', { name: 'Start', exact: true }).click()
  await expect.poll(() => m.posts.length).toBe(1)
  expect(m.posts[0].body).toMatchObject({ engine: 'ollama', model: 'gemma4:12b' })
})

test('model picker: when the engine list fails to load, says the default model is used', async ({ page }) => {
  const m = await mockLive(page, { enginesFail: true })
  const live = await openLive(page)
  await expect(live.getByTestId('live-model-note')).toHaveText("Couldn't load the model list; the engine's default model will be used.")
  await expect(live.getByLabel('Model', { exact: true })).toHaveCount(0)
  await expect(live.getByRole('button', { name: 'Start', exact: true })).toBeDisabled()
  expect(m.posts).toEqual([])
})

test('the transcript shows first, then the translation fills the same line', async ({ page }) => {
  const m = await mockLive(page)
  const live = await openLive(page)
  await live.getByLabel('Stream link', { exact: true }).fill('https://www.youtube.com/watch?v=dQw4w9WgXcQ')
  await live.getByRole('button', { name: 'Start', exact: true }).click()
  m.state.status = 'running'
  m.state.message = 'Listening'
  m.state.cues = [cue(0), pendingCue(1)]
  const items = live.getByRole('list', { name: 'Live lines, newest first' }).getByRole('listitem')
  await expect(items).toHaveCount(2)
  await expect(items.first()).toContainText('第1句台词')
  await expect(items.first().getByTestId('live-translating')).toHaveText('translating…')
  await expect(items.last().getByTestId('live-translating')).toHaveCount(0)

  // The translation lands on line 1 only; the reader asks again from that line.
  m.state.cues = [cue(0), cue(1)]
  await expect(items.first()).toContainText('Line 1:')
  await expect(items).toHaveCount(2)
  expect(m.polls).toContain('after=1')
})

test('a stopped session leaves the transcript with a plain note', async ({ page }) => {
  const m = await mockLive(page)
  const live = await openLive(page)
  await live.getByLabel('Stream link', { exact: true }).fill('https://www.youtube.com/watch?v=dQw4w9WgXcQ')
  await live.getByRole('button', { name: 'Start', exact: true }).click()
  m.state.status = 'running'
  m.state.cues = [pendingCue(0)]
  await expect(live.getByTestId('live-translating')).toBeVisible()
  m.state.status = 'cancelled'
  m.state.cues = [{ ...pendingCue(0), translation: 'cancelled' }]
  await expect(live.getByTestId('live-untranslated')).toContainText('Not translated')
  await expect(live.getByText('第0句台词')).toBeVisible()
})

test('a switch inside a field keeps its own size', async ({ page }) => {
  await mockLive(page)
  const live = await openLive(page)
  await live.getByText('Advanced').click()
  await expectSwitchKeepsItsSize(live)
})
