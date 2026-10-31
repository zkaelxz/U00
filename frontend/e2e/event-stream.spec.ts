import { createServer, type ServerResponse } from 'node:http'
import type { AddressInfo } from 'node:net'

import { expect, test, type Page } from '@playwright/test'

import { SID, cue, mockLive, openLive } from './liveMocks'
import { runClock } from './mockHelpers'
import { withTranslateLines } from './stageLineMocks'

// Push updates (GET /api/events): a job's progress and the bell's list arrive
// over the stream, with no polling while it is up; a drop reconnects and
// re-reads once; the page falls back to polling when the stream can't be
// opened. The stream is mocked here (the seeded e2e API answers it with 429,
// see serve_seeded_api.py) by a local SSE server the request is continued to.

const job = (status: string, progress: number, message: string) => ({
  job_id: 'fake-job', status, progress, message, error: null, description: null,
  gpu_touching: false, started_at: 1, finished_at: status === 'done' ? 2 : null, updated_at: 1,
  result: null, outcome: status === 'done' ? 'ok' : null, outcome_message: status === 'done' ? 'Finished.' : null,
})

// The Translate stage stands in for any stage with a job: its run is mocked,
// so nothing translates.
async function mockRun(page: Page, server: { job: ReturnType<typeof job> }) {
  const gets: number[] = []
  await withTranslateLines(page)
  await page.route('**/api/translate-run/dramas/1/run', (route) => route.fulfill({
    json: { job_id: 'fake-job', drama_id: 1, engine: 'x', model: null, target_line_count: 3, fallback_engines: [] },
  }))
  await page.route('**/api/jobs/fake-job', (route) => {
    gets.push(Date.now())
    return route.fulfill({ json: server.job })
  })
  return gets
}

const startRun = (page: Page) =>
  page.getByRole('region', { name: 'Translate run' }).getByRole('button', { name: /^Translate \d+ lines?$/ }).click()

// A real, held-open SSE connection: a local server the browser's /api/events
// request is continued to, so events can be pushed one at a time while the
// connection stays up (a fulfilled route delivers its whole body at once).
async function sseServer() {
  const clients: ServerResponse[] = []
  const server = createServer((req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' })
    res.write('retry: 3000\n\n')
    res.write(`event: ready\ndata: ${JSON.stringify({ topics: ['jobs', 'notifications', 'live'] })}\n\n`)
    clients.push(res)
    req.on('close', () => clients.splice(clients.indexOf(res), 1))
  })
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r))
  const port = (server.address() as AddressInfo).port
  return {
    url: `http://127.0.0.1:${port}/api/events`,
    clients,
    push: (name: string, data: unknown) => {
      for (const c of clients) c.write(`event: ${name}\ndata: ${JSON.stringify(data)}\n\n`)
    },
    ping: () => clients.forEach((c) => c.write('event: ping\ndata: {}\n\n')),
    close: () => new Promise<void>((r) => {
      clients.forEach((c) => c.end())
      server.close(() => r())
    }),
  }
}

test('job progress arrives over the stream, with no polling while it is up', async ({ page }) => {
  const stream = await sseServer()
  try {
    const server = { job: job('running', 0.1, 'starting') }
    const gets = await mockRun(page, server)
    await page.route('**/api/events?*', (route) => route.continue({ url: stream.url }))

    await page.goto('/#/drama/1/translate')
    await expect.poll(() => stream.clients.length).toBe(1)
    await page.clock.install()
    await startRun(page)
    const status = page.getByTestId('job-status')
    await expect(status).toContainText('Running · starting')
    const reads = gets.length

    // The server's GET would still say 10%: what the page shows next can only
    // have come over the stream.
    stream.push('job', job('running', 0.6, 'line 6 of 10'))
    await expect(status).toContainText('Running · line 6 of 10')
    await expect(page.getByTestId('job-percent')).toHaveText('60%')
    stream.ping()
    stream.push('job', { ...job('running', 0.9, 'other job'), job_id: 'someone-else' })

    // No timer: over three seconds (two poll intervals) no GET of the job.
    // Proving a non-event: a polling timer would fire within two of its intervals.
    await runClock(page, 3000)
    expect(gets.length).toBe(reads)
    await expect(page.getByTestId('job-percent')).toHaveText('60%')

    stream.push('job', job('done', 1, 'all lines spoken'))
    await expect(status).toContainText('Done · all lines spoken')
    await expect(page.getByTestId('job-outcome')).toContainText('Finished')
    expect(gets.length).toBe(reads)
  } finally {
    await stream.close()
  }
})

test('a dropped stream reconnects and re-reads the job once', async ({ page }) => {
  const stream = await sseServer()
  try {
    const server = { job: job('running', 0.2, 'working') }
    const gets = await mockRun(page, server)
    await page.route('**/api/events?*', (route) => route.continue({ url: stream.url }))
    await page.goto('/#/drama/1/translate')
    await expect.poll(() => stream.clients.length).toBe(1)
    await startRun(page)
    await expect(page.getByTestId('job-status')).toContainText('Running · working')

    // The job finishes while the connection is down: the resync GET after
    // the reconnect is what shows it.
    const reads = gets.length
    server.job = job('done', 1, 'finished while away')
    stream.clients.forEach((c) => c.destroy())
    await expect(page.getByTestId('job-status')).toContainText('Done · finished while away')
    expect(gets.length).toBe(reads + 1)
  } finally {
    await stream.close()
  }
})

test('falls back to polling when the stream cannot be opened', async ({ page }) => {
  const server = { job: job('running', 0.3, 'working') }
  const gets = await mockRun(page, server)
  await page.route('**/api/events?*', (route) =>
    route.fulfill({ status: 429, json: { error: { code: 'rate_limited', message: 'Too many.' } } }))
  await page.goto('/#/drama/1/translate')
  await startRun(page)
  await expect(page.getByTestId('job-status')).toContainText('Running · working')
  await expect.poll(() => gets.length, { timeout: 8000 }).toBeGreaterThanOrEqual(3)
  server.job = job('done', 1, 'finished by polling')
  await expect(page.getByTestId('job-status')).toContainText('Done · finished by polling')
})

test('the bell shows a pushed notification without polling', async ({ page }) => {
  const stream = await sseServer()
  try {
    let listGets = 0
    await page.route('**/api/notifications', (route) => {
      listGets += 1
      return route.fulfill({ json: { items: [] } })
    })
    await page.route('**/api/events?*', (route) => route.continue({ url: stream.url }))
    await page.goto('/#/')
    await expect.poll(() => stream.clients.length).toBe(1)
    const bell = page.getByRole('button', { name: /^Notifications/ })
    await expect(bell).toHaveAccessibleName('Notifications')
    const reads = listGets
    stream.push('notifications', {
      items: [{ id: Date.now(), at: Date.now() / 1000, kind: 'job_done', text: 'Finished: Translation (pushed)' }],
    })
    await expect(bell).toHaveAccessibleName('Notifications (1 new)')
    await bell.click()
    await expect(page.getByText('Finished: Translation (pushed)')).toBeVisible()
    expect(listGets).toBe(reads)
  } finally {
    await stream.close()
  }
})

test('Live: a pushed status reads the new lines from the page cursor, no timer', async ({ page }) => {
  const stream = await sseServer()
  try {
    const m = await mockLive(page)
    await page.route('**/api/events?*', (route) => route.continue({ url: stream.url }))
    const live = await openLive(page)
    await expect.poll(() => stream.clients.length).toBe(1)
    await page.clock.install()
    await live.getByLabel('Stream link', { exact: true }).fill('https://www.youtube.com/watch?v=abc')
    await live.getByRole('button', { name: 'Start', exact: true }).click()
    await expect(live.getByTestId('live-status')).toHaveText('Waiting for the GPU')

    m.state.status = 'running'
    m.state.message = 'Listening'
    m.state.cues = [cue(0), cue(1)]
    const reads = m.polls.length
    const status = { session_id: SID, status: 'running', message: 'Listening', progress: 0, cues: [], next_index: 2 }
    stream.push('live', status)
    await expect(live.getByTestId('live-status')).toHaveText('Listening · 2 lines')
    expect(m.polls.slice(reads)).toEqual(['after=0'])

    // Status only (no new lines): shown without a read.
    stream.push('live', { ...status, message: 'Still listening' })
    await expect(live.getByTestId('live-status')).toHaveText('Still listening · 2 lines')
    // Proving a non-event: a status-only push must not trigger a poll, which would fire within this window.
    await runClock(page, 2500)
    expect(m.polls.slice(reads)).toEqual(['after=0'])

    m.state.cues = [cue(0), cue(1), cue(2)]
    stream.push('live', { ...status, next_index: 3 })
    await expect(live.getByTestId('live-status')).toHaveText('Listening · 3 lines')
    expect(m.polls.slice(reads)).toEqual(['after=0', 'after=2'])
    expect(m.unmocked).toEqual([])
  } finally {
    await stream.close()
  }
})
