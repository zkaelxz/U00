import { createServer, type ServerResponse } from 'node:http'
import type { AddressInfo } from 'node:net'

import { expect, test } from './fixtures'
import { mockJobsApi, pageJobs } from './jobsMenuMocks'
import { openMenu } from './settingsNav'

// The Jobs page (#/jobs) with /api/jobs mocked: table, filters, states,
// Cancel, PC-only Delete, row details, the queue line and the member and
// remote-admin personas.

const rows = (page: import('@playwright/test').Page) => page.getByTestId('jobs-table').locator('tbody > tr[data-testid^="job-row-"]')
const chip = (page: import('@playwright/test').Page, name: string) => page.getByRole('group', { name: 'Status' }).getByRole('button', { name })
const error = (status: number, message: string) => ({ status, json: { error: { code: 'x', message } } })

test('running jobs first, with title and stage links, a progress bar and live duration', async ({ page }) => {
  await mockJobsApi(page, pageJobs(), true)
  await page.goto('/#/jobs')
  await expect(page.getByRole('heading', { name: 'Jobs', level: 1 })).toBeVisible()
  // Active is the default while anything is active: the running job, then the queued one.
  await expect(rows(page)).toHaveCount(2)
  await expect(rows(page).nth(0)).toContainText('Translate Signal')
  await expect(rows(page).nth(1)).toContainText('Dub Kae')
  const first = rows(page).first()
  await expect(first.getByRole('link', { name: 'Signal', exact: true })).toHaveAttribute('href', '#/drama/3')
  await expect(first.getByRole('link', { name: 'Translate', exact: true })).toHaveAttribute('href', '#/drama/3/translate')
  await expect(first.getByRole('link', { name: 'Open translate for Translate Signal' })).toHaveAttribute('href', '#/drama/3/translate')
  await expect(first.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '40')
  await expect(first).toContainText('40%')
  await expect(first).toContainText('Batch 2 of 5')
  // Not tied to a viewer without auth: no Who column.
  await expect(page.getByRole('columnheader', { name: 'Who' })).toHaveCount(0)
  const duration = first.locator('td').nth(6)
  const before = await duration.textContent()
  await expect.poll(async () => await duration.textContent(), { timeout: 5000 }).not.toBe(before)

  await chip(page, 'All').click()
  await expect(rows(page)).toHaveCount(5)
  await expect(rows(page).nth(2)).toContainText('Transcribe Signal')
  // No Retry (D8): a finished job links to its stage instead.
  await expect(page.getByRole('button', { name: /retry/i })).toHaveCount(0)
  const exportRow = page.getByTestId('job-row-burned_video_4')
  await expect(exportRow.getByRole('link', { name: 'Export', exact: true })).toHaveAttribute('href', '#/drama/4/export')
  await expect(exportRow).toContainText('Disk full')
  // A job tied to no title has nothing to link.
  await expect(page.getByTestId('job-row-lncrawl_9').getByRole('link')).toHaveCount(0)
})

test('a title-less job links its Stage cell to the page the server names', async ({ page }) => {
  const t = Math.floor(Date.now() / 1000)
  const base = { progress: null, message: '', error: null, gpu_touching: false, updated_at: t, owned_by_me: true, drama_id: null, kind: 'other', status: 'done', outcome: 'ok', started_at: t - 60, finished_at: t - 50 }
  await mockJobsApi(page, [
    { ...base, job_id: 'sources_search', description: 'Search sources', page: 'sources' },
    { ...base, job_id: 'deno_install', description: 'Install Deno', page: 'diagnostics' },
    { ...base, job_id: 'benchmark_lab', description: 'Benchmark', page: null },
  ], true)
  await page.goto('/#/jobs')
  await chip(page, 'All').click()
  await expect(page.getByTestId('job-row-sources_search').getByRole('link', { name: 'Sources', exact: true })).toHaveAttribute('href', '#/sources')
  await expect(page.getByTestId('job-row-deno_install').getByRole('link', { name: 'Diagnostics', exact: true })).toHaveAttribute('href', '#/diagnostics?card=packages')
  await expect(page.getByTestId('job-row-benchmark_lab').getByRole('link')).toHaveCount(0)
})

test('status chips show counts and filters combine, then are remembered', async ({ page }) => {
  await mockJobsApi(page, pageJobs(), true)
  await page.goto('/#/jobs')
  await expect(chip(page, /^Active/)).toContainText('2')
  await expect(chip(page, /^Failed/)).toContainText('1')
  await expect(chip(page, /^Finished/)).toContainText('2')
  await expect(chip(page, /^All/)).toContainText('5')

  await chip(page, /^Failed/).click()
  await expect(rows(page)).toHaveCount(1)
  await expect(rows(page).first()).toContainText('Export Kae')

  await chip(page, /^All/).click()
  await page.getByLabel('Search jobs').fill('signal')
  await expect(rows(page)).toHaveCount(2)
  await page.getByLabel('Search jobs').fill('')
  await page.getByLabel('Kind').selectOption('sources')
  await expect(rows(page)).toHaveCount(1)
  await expect(rows(page).first()).toContainText('Import novel')
  await page.getByLabel('Time range').selectOption('week')
  await expect(page.getByTestId('jobs-no-match')).toContainText('No jobs match.')
  await page.getByTestId('jobs-no-match').getByRole('button', { name: 'Clear filters' }).click()
  await expect(rows(page)).toHaveCount(5)

  await chip(page, /^Finished/).click()
  await page.getByLabel('Kind').selectOption('transcribe')
  await page.reload()
  await expect(chip(page, /^Finished/)).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByLabel('Kind')).toHaveValue('transcribe')
  await expect(rows(page)).toHaveCount(1)
})

test('a cancelled job shows under All and under Cancelled, and the chips add up', async ({ page }) => {
  const t = Math.floor(Date.now() / 1000)
  await mockJobsApi(page, [
    ...pageJobs(),
    { progress: null, message: 'Cancelled.', error: null, gpu_touching: false, updated_at: t - 30, owned_by_me: true, job_id: 'translate_5', status: 'cancelled', description: 'Translate Moon', started_at: t - 90, finished_at: t - 30, drama_id: 5, kind: 'translate', outcome: 'cancelled' },
  ], true)
  await page.goto('/#/jobs')
  await expect(chip(page, /^Cancelled/)).toContainText('1')
  await expect(chip(page, /^Finished/)).toContainText('2')
  await expect(chip(page, /^All/)).toContainText('6')
  await chip(page, /^All/).click()
  await expect(page.getByTestId('job-row-translate_5')).toContainText('Cancelled')
  await chip(page, /^Cancelled/).click()
  await expect(rows(page)).toHaveCount(1)
  await expect(rows(page).first()).toContainText('Translate Moon')
  await chip(page, /^Finished/).click()
  await expect(page.getByTestId('job-row-translate_5')).toHaveCount(0)
})

test('sorting by Started reverses the order within the finished jobs', async ({ page }) => {
  await mockJobsApi(page, pageJobs(), true)
  await page.goto('/#/jobs')
  await chip(page, /^Finished/).click()
  await expect(rows(page).first()).toContainText('Transcribe Signal')
  await page.getByRole('button', { name: 'Started' }).click()
  await expect(page.getByRole('columnheader', { name: 'Started' })).toHaveAttribute('aria-sort', 'descending')
  await page.getByRole('button', { name: 'Started' }).click()
  await expect(page.getByRole('columnheader', { name: 'Started' })).toHaveAttribute('aria-sort', 'ascending')
  await expect(rows(page).first()).toContainText('Import novel')
})

test('empty: no jobs ever, with a link to Library', async ({ page }) => {
  await mockJobsApi(page, [], true)
  await page.goto('/#/jobs')
  const empty = page.getByTestId('jobs-empty')
  await expect(empty).toContainText('No jobs yet. Jobs appear here when you transcribe, translate or export.')
  await expect(empty.getByRole('link', { name: 'Go to Library' })).toHaveAttribute('href', '#/library')
})

test('error: a failed read keeps the last list and shows a banner', async ({ page }) => {
  let failing = false
  await mockJobsApi(page, pageJobs(), true)
  await page.route((u) => u.pathname === '/api/jobs', (route) =>
    failing ? route.fulfill(error(500, 'boom')) : route.fallback())
  await page.goto('/#/jobs')
  await expect(rows(page)).toHaveCount(2)
  failing = true
  await page.evaluate(() => window.dispatchEvent(new Event('focus')))
  await expect(page.getByRole('alert')).toBeVisible()
  await expect(rows(page)).toHaveCount(2)
})

test('error on the first read: a banner, not a blank page', async ({ page }) => {
  await page.route((u) => u.pathname === '/api/jobs', (route) => route.fulfill(error(500, 'boom')))
  await page.goto('/#/jobs')
  await expect(page.getByRole('alert')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Jobs', level: 1 })).toBeVisible()
})

test('404: an older server says it does not list jobs, and the header button hides', async ({ page }) => {
  await page.route((u) => u.pathname === '/api/jobs', (route) => route.fulfill(error(404, 'Not found')))
  await page.goto('/#/jobs')
  await expect(page.getByTestId('jobs-refused')).toHaveText("This server doesn't list jobs.")
  await expect(page.getByRole('button', { name: /^Jobs/ })).toHaveCount(0)
})

test('403: says the viewer cannot see jobs here', async ({ page }) => {
  await page.route((u) => u.pathname === '/api/jobs', (route) => route.fulfill(error(403, 'No.')))
  await page.goto('/#/jobs')
  await expect(page.getByTestId('jobs-refused')).toHaveText("You can't see jobs here.")
})

test('Cancel stops a running job; a refused cancel shows the server message', async ({ page }) => {
  const log = await mockJobsApi(page, pageJobs(), true)
  await page.goto('/#/jobs')
  // Past 10% progress, Cancel asks first; the queued Dub below is one click.
  await rows(page).first().getByRole('button', { name: 'Cancel Translate Signal' }).click()
  expect(log.cancelled).toEqual([])
  await rows(page).first().getByRole('button', { name: 'Confirm cancel' }).click()
  await expect.poll(() => log.cancelled).toEqual(['translate_3'])
  // The next read says cancelled, so the job leaves Active.
  await expect(rows(page)).toHaveCount(1)
  await expect(rows(page).first()).toContainText('Dub Kae')

  await page.route('**/api/jobs/dub_4/cancel', (route) =>
    route.fulfill({ status: 409, json: { error: { code: 'conflict', message: 'Already finishing.' } } }))
  await rows(page).first().getByRole('button', { name: 'Cancel Dub Kae' }).click()
  await expect(page.getByRole('alert')).toBeVisible()
  await expect(rows(page)).toHaveCount(1)
})

test('Force stop shows only for a job stuck in Cancelling, and needs two presses', async ({ page }) => {
  const jobs = pageJobs().map((j) => (j.job_id === 'translate_3' ? { ...j, message: 'Cancelling... (finishes the current step first)', can_force_stop: true } : j))
  const log = await mockJobsApi(page, jobs, true)
  await page.goto('/#/jobs')
  await expect(rows(page)).toHaveCount(2)
  await expect(rows(page).nth(1).getByRole('button', { name: /Force stop/ })).toHaveCount(0)
  const stuck = rows(page).first()
  await expect(stuck).toContainText('its worker keeps running in the background')
  await stuck.getByRole('button', { name: 'Force stop Translate Signal' }).click()
  expect(log.forceStopped).toEqual([])
  await stuck.getByRole('button', { name: 'Confirm force stop' }).click()
  await expect.poll(() => log.forceStopped).toEqual(['translate_3'])
  await expect(rows(page)).toHaveCount(1)
})

test('a finished row\'s Actions controls share one size and variant', async ({ page }) => {
  await mockJobsApi(page, pageJobs(), true)
  await page.goto('/#/jobs')
  await chip(page, 'All').click()
  const row = rows(page).filter({ hasText: 'Transcribe Signal' }).first()
  const controls = [
    row.getByRole('link', { name: /^Open .* for Transcribe Signal/ }),
    row.getByRole('button', { name: 'Details for Transcribe Signal' }),
    row.getByRole('button', { name: 'Delete Transcribe Signal' }),
  ]
  const heights: number[] = []
  for (const c of controls) {
    await expect(c).toHaveClass(/btn-secondary/)
    await expect(c).toHaveClass(/btn-sm/)
    heights.push((await c.boundingBox())!.height)
  }
  expect(new Set(heights.map(Math.round)).size).toBe(1)
})

test('Delete is two-step and permanent; only finished jobs offer it', async ({ page }) => {
  const log = await mockJobsApi(page, pageJobs(), true)
  await page.goto('/#/jobs')
  await expect(rows(page).first().getByRole('button', { name: /^Delete/ })).toHaveCount(0)
  await chip(page, 'All').click()
  await expect(page.getByRole('button', { name: /^Delete Translate Signal/ })).toHaveCount(0)

  const titleHeader = page.getByRole('columnheader', { name: 'Title' })
  const widthBefore = (await titleHeader.boundingBox())!.width
  await page.getByRole('button', { name: 'Delete Transcribe Signal' }).click()
  expect(log.deleted).toEqual([])
  // The long job title must not widen the Actions column and squeeze the table.
  expect(Math.abs((await titleHeader.boundingBox())!.width - widthBefore)).toBeLessThan(24)
  await page.getByRole('button', { name: 'Confirm delete', exact: true }).click()
  await expect.poll(() => log.deleted).toEqual(['transcribe_3'])
  await expect(rows(page)).toHaveCount(4)

  await page.getByRole('button', { name: 'Delete all finished jobs' }).click()
  await expect(page.getByTestId('jobs-clear')).toContainText('Permanent')
  await page.getByRole('button', { name: /Confirm delete 2 finished/ }).click()
  await expect.poll(() => log.cleared).toBe(1)
  await expect(rows(page)).toHaveCount(2)
  await expect(page.getByTestId('jobs-clear')).toHaveCount(0)
})

test('details show the full time and stage timing, fetched once on first open', async ({ page }) => {
  const log = await mockJobsApi(page, [
    { ...pageJobs()[2], message: 'Transcribing... 99%' },
    pageJobs()[3],
  ], true)
  await page.goto('/#/jobs')
  expect(log.stages).toEqual([])
  const row = page.getByTestId('job-row-transcribe_3')
  // A finished job's last progress text would read as stuck.
  await expect(row).not.toContainText('Transcribing... 99%')
  await row.getByRole('button', { name: 'Details for Transcribe Signal' }).click()
  const details = page.getByTestId('job-details-transcribe_3')
  await expect(details.getByText('Started')).toBeVisible()
  await expect(details).toContainText('Finished: 120 lines')
  const stages = details.getByRole('list', { name: 'Time by stage' })
  await expect(stages.locator('li')).toHaveCount(2)
  await expect(stages).toContainText('Translating')
  await expect(details).toContainText('Total 1 min 30 s · estimated $0.03')
  await expect.poll(() => log.stages).toEqual(['transcribe_3'])
  await row.getByRole('button', { name: 'Details for Transcribe Signal' }).click()
  await expect(details).toHaveCount(0)
  await row.getByRole('button', { name: 'Details for Transcribe Signal' }).click()
  await expect(details).toBeVisible()

  const failed = page.getByTestId('job-row-burned_video_4')
  await failed.getByRole('button', { name: 'Details for Export Kae' }).click()
  await expect(page.getByTestId('job-details-burned_video_4')).toContainText('Disk full')
})

test('the Jobs page and the header popover share one list and one read', async ({ page }) => {
  let reads = 0
  await mockJobsApi(page, pageJobs(), true)
  await page.route((u) => u.pathname === '/api/jobs', (route) => {
    reads += 1
    return route.fallback()
  })
  await page.goto('/#/jobs')
  await expect(rows(page)).toHaveCount(2)
  await expect(page.getByRole('button', { name: 'Jobs (2 jobs running)' })).toBeVisible()
  // The popover only reads the list the page already holds.
  await page.getByRole('button', { name: 'Jobs (2 jobs running)' }).click()
  await expect(page.getByRole('region', { name: 'Jobs' }).locator('.jobs-item')).toHaveCount(5)
  expect(reads).toBe(1)
})

test('one push stream updates the table and the header badge', async ({ page }) => {
  const clients: ServerResponse[] = []
  const server = createServer((req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' })
    res.write('retry: 3000\n\n')
    res.write(`event: ready\ndata: ${JSON.stringify({ topics: ['jobs'] })}\n\n`)
    clients.push(res)
    req.on('close', () => clients.splice(clients.indexOf(res), 1))
  })
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r))
  const url = `http://127.0.0.1:${(server.address() as AddressInfo).port}/api/events`
  try {
    await mockJobsApi(page, pageJobs(), true)
    await page.route('**/api/events?*', (route) => route.continue({ url }))
    await page.goto('/#/jobs')
    await expect.poll(() => clients.length).toBe(1)
    await expect(page.getByTestId('jobs-count')).toHaveText('2')
    const push = (name: string, data: unknown) => clients.forEach((c) => c.write(`event: ${name}\ndata: ${JSON.stringify(data)}\n\n`))

    push('job', { ...pageJobs()[0], progress: 0.9, message: 'Batch 5 of 5' })
    await expect(rows(page).first()).toContainText('90%')
    push('job', { ...pageJobs()[0], job_id: 'new_1', description: 'Translate Fresh', status: 'running', drama_id: null, kind: 'other' })
    await expect(page.getByTestId('jobs-count')).toHaveText('3')
    await expect(rows(page)).toHaveCount(3)
    push('job_gone', { job_id: 'new_1' })
    await expect(page.getByTestId('jobs-count')).toHaveText('2')
    await expect(rows(page)).toHaveCount(2)
    await expect(page.getByTestId('jobs-polling')).toHaveCount(0)
  } finally {
    clients.forEach((c) => c.end())
    await new Promise<void>((r) => server.close(() => r()))
  }
})

test('a GET read before a job finished, landing after its "done" push, does not put it back to running', async ({ page }) => {
  const clients: ServerResponse[] = []
  const server = createServer((req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' })
    res.write('retry: 3000\n\n')
    res.write(`event: ready\ndata: ${JSON.stringify({ topics: ['jobs'] })}\n\n`)
    clients.push(res)
    req.on('close', () => clients.splice(clients.indexOf(res), 1))
  })
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r))
  const url = `http://127.0.0.1:${(server.address() as AddressInfo).port}/api/events`
  try {
    await mockJobsApi(page, pageJobs(), true)
    await page.route('**/api/events?*', (route) => route.continue({ url }))
    let holding = false
    let reads = 0
    let release!: () => void
    const held = new Promise<void>((r) => (release = r))
    await page.route((u) => u.pathname === '/api/jobs', async (route) => {
      if (!holding) return route.fallback()
      reads += 1
      // The server read this list while the job was still running.
      await held
      return route.fulfill({ json: { items: pageJobs() } })
    })
    await page.goto('/#/jobs')
    await expect.poll(() => clients.length).toBe(1)
    await expect(page.getByTestId('jobs-count')).toHaveText('2')

    holding = true
    await page.evaluate(() => window.dispatchEvent(new Event('focus')))
    await expect.poll(() => reads).toBe(1)
    const push = (name: string, data: unknown) => clients.forEach((c) => c.write(`event: ${name}\ndata: ${JSON.stringify(data)}\n\n`))
    push('job', { ...pageJobs()[0], status: 'done', outcome: 'ok', progress: 1, finished_at: Math.floor(Date.now() / 1000) })
    await expect(page.getByTestId('jobs-count')).toHaveText('1')

    const landed = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/jobs')
    release()
    await landed
    await page.evaluate(() => new Promise((r) => setTimeout(r, 200)))
    await expect(page.getByTestId('jobs-count')).toHaveText('1')
  } finally {
    clients.forEach((c) => c.end())
    await new Promise<void>((r) => server.close(() => r()))
  }
})

test('the stream down: the list still loads and no polling line is shown', async ({ page }) => {
  await mockJobsApi(page, pageJobs(), true)
  await page.route('**/api/events?*', (route) => route.fulfill({ status: 429, json: { error: { code: 'x', message: 'no' } } }))
  await page.goto('/#/jobs')
  await expect(page.getByTestId('jobs-count')).toHaveText('2')
  await expect(page.getByTestId('jobs-polling')).toHaveCount(0)
})

const me = (permissions: string[], user: Record<string, unknown>) => ({
  auth_enabled: true, signed_in: true, sign_in_configured: true, zone: 'internet', permissions,
  user: { id: 1, email: 'owner@example.com', display_name: 'Owner', is_admin: true, is_local_owner: false, ...user },
})

test('remote admin: Cancel on every job, no Delete, and Who and Mine show', async ({ page }) => {
  const log = await mockJobsApi(page, pageJobs(), true)
  await page.route('**/api/meta', (route) => route.fulfill({ json: { app: 'Baihe Studio', api_version: '0.1', environment: 'production', local: false } }))
  await page.route('**/api/auth/me', (route) => route.fulfill({ json: me(['library.read', 'jobs.cancel', 'admin.users.read'], {}) }))
  await page.goto('/#/jobs')
  await expect(rows(page)).toHaveCount(2)
  await expect(page.getByRole('button', { name: 'Cancel Translate Signal' })).toBeVisible()
  // Owner decision 2026-10-12: an admin may stop a runaway job from away.
  await expect(page.getByRole('button', { name: 'Cancel Dub Kae' })).toBeVisible()
  await expect(page.getByTestId('jobs-cancel-note')).toHaveCount(0)
  await expect(page.getByRole('columnheader', { name: 'Who' })).toBeVisible()
  await expect(page.getByTestId('job-row-dub_4')).toContainText('Someone else')
  await expect(page.getByTestId('job-row-translate_3')).toContainText('You')

  await chip(page, 'All').click()
  await expect(page.getByRole('button', { name: /^Delete/ })).toHaveCount(0)
  await expect(page.getByTestId('jobs-clear')).toHaveCount(0)
  await expect(page.getByText('Deleting is PC only.')).toHaveCount(0)

  await page.getByLabel('Mine').check()
  await expect(rows(page)).toHaveCount(4)
  await expect(page.getByTestId('job-row-dub_4')).toHaveCount(0)
  expect(log.cancelled).toEqual([])
})

test('member: Cancel only on their own jobs, with the rule', async ({ page }) => {
  await mockJobsApi(page, pageJobs(), true)
  await page.route('**/api/meta', (route) => route.fulfill({ json: { app: 'Baihe Studio', api_version: '0.1', environment: 'production', local: false } }))
  await page.route('**/api/auth/me', (route) => route.fulfill({ json: me(['library.read', 'jobs.cancel'], { is_admin: false }) }))
  await page.goto('/#/jobs')
  await expect(page.getByRole('button', { name: 'Cancel Translate Signal' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Cancel Dub Kae' })).toHaveCount(0)
  await expect(page.getByTestId('jobs-cancel-note')).toHaveText('Only the person who started a job, the title’s owner or an admin can cancel it.')
})

test('the queue: counts for everyone, and the place of each of my waiting jobs', async ({ page }) => {
  const t = Math.floor(Date.now() / 1000)
  const jobs = pageJobs()
  jobs.push({ ...jobs[1], job_id: 'dub_3', description: 'Dub Signal', drama_id: 3, owned_by_me: true, started_at: t, updated_at: t })
  await mockJobsApi(page, jobs, true)
  await page.goto('/#/jobs')
  await expect(page.getByTestId('jobs-queue-line')).toHaveText('1 job running (all lanes), 2 waiting in the GPU queue')
  await expect(page.getByTestId('job-place-dub_3')).toHaveText('Waiting: #2 in the GPU queue')
  // Someone else's waiting job shows no place, and a running job none either.
  await expect(page.getByTestId('job-place-dub_4')).toHaveCount(0)
  await expect(page.getByTestId('job-place-translate_3')).toHaveCount(0)
})

test('PC owner gets Cancel on every job', async ({ page }) => {
  await mockJobsApi(page, pageJobs(), true)
  await page.route('**/api/meta', (route) => route.fulfill({ json: { app: 'Baihe Studio', api_version: '0.1', environment: 'production', local: true } }))
  await page.route('**/api/auth/me', (route) => route.fulfill({ json: me(['library.read', 'jobs.cancel', 'admin.library', 'admin.users.read'], { is_local_owner: true }) }))
  await page.goto('/#/jobs')
  await expect(page.getByRole('button', { name: 'Cancel Dub Kae' })).toBeVisible()
  await expect(page.getByTestId('jobs-cancel-note')).toHaveCount(0)
})

test('the menu has Jobs with a running count, and it opens the page', async ({ page }) => {
  await mockJobsApi(page, pageJobs(), true)
  await page.goto('/#/library')
  await openMenu(page)
  // The count badge is part of the link's name, so match by prefix: the rail at 1024px and up, the drawer below.
  const link = page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: /^Jobs/ })
  await expect(page.getByTestId('rail-jobs-count')).toHaveText('2')
  await link.click()
  await expect(page).toHaveURL(/#\/jobs$/)
  await openMenu(page)
  await expect(link).toHaveAttribute('aria-current', 'page')
})

// A worker that returned, but whose work failed: the server calls it done and the outcome says failed.
function failedTranscription() {
  const t = Math.floor(Date.now() / 1000)
  const base = { progress: null, message: '', error: null, gpu_touching: false, updated_at: t, owned_by_me: true, page: null, kind: 'transcribe', drama_id: 3 }
  return [
    { ...base, job_id: 'transcribe_3', status: 'done', description: 'Transcription (title #3)', started_at: t - 120, finished_at: t - 100, outcome: 'failed', outcome_message: 'The speech model could not be downloaded.' },
    { ...base, job_id: 'translate_3', status: 'done', description: 'Translation (title #3)', started_at: t - 90, finished_at: t - 60, outcome: 'ok', outcome_message: 'Finished.' },
    { ...base, job_id: 'ocr_4', status: 'error', description: 'Audio emotion tags (drama #4)', error: 'Audio emotion tags need: pip install funasr', started_at: t - 50, finished_at: t - 40, outcome: 'failed', outcome_message: 'Audio emotion tags need: pip install funasr', drama_id: 4, kind: 'other' },
  ]
}

test('a done job whose outcome failed reads Failed in the row, the details, the filters and the menu', async ({ page }) => {
  await mockJobsApi(page, failedTranscription(), true)
  await page.goto('/#/jobs')
  await chip(page, 'All').click()
  const row = page.getByTestId('job-row-transcribe_3')
  await expect(row.locator('.pill')).toHaveText('Failed')
  await expect(row).not.toContainText('Done')
  // The reason alone: the pill already says Failed.
  await expect(row).toContainText('The speech model could not be downloaded.')
  await expect(row).not.toContainText('Failed: ')
  await expect(chip(page, /^Failed/)).toContainText('2')
  await expect(chip(page, /^Finished/)).toContainText('1')
  await chip(page, /^Failed/).click()
  await expect(rows(page)).toHaveCount(2)

  await page.getByRole('button', { name: /^Details for Transcription/ }).first().click()
  await expect(page.getByTestId('job-details-transcribe_3')).toContainText('The speech model could not be downloaded.')
  await expect(page.getByTestId('job-details-transcribe_3').locator('p.error')).toHaveCount(1)

  await page.getByRole('button', { name: /^Jobs/ }).click()
  const panel = page.locator('.jobs-panel')
  const item = panel.locator('li', { hasText: 'Transcription - Signal' })
  await expect(item.locator('.pill')).toHaveText('Failed')
})

test('"Finished" is said once, titles are named by their name, and a pip instruction points at Diagnostics', async ({ page }) => {
  await mockJobsApi(page, failedTranscription(), true)
  await page.goto('/#/jobs')
  await chip(page, 'All').click()
  const done = page.getByTestId('job-row-translate_3')
  await expect(done.locator('.pill')).toHaveText('Done')
  await expect(done).not.toContainText('Finished: Finished')
  await expect(done).not.toContainText('Finished.')
  await expect(done.getByRole('link', { name: 'Signal', exact: true })).toBeVisible()
  await expect(done.locator('.jobs-name strong')).toHaveText('Translation')
  const body = page.getByTestId('jobs-table')
  await expect(body).not.toContainText('drama')
  await expect(body).not.toContainText('#3')
  const pip = page.getByTestId('job-row-ocr_4')
  await expect(pip).toContainText('Diagnostics > Packages and installs (funasr)')
  await expect(pip).not.toContainText('pip install')
  await expect(pip.locator('.jobs-name strong')).toHaveText('Audio emotion tags')
})

for (const scheme of ['light', 'dark'] as const) {
  test(`${scheme}: Progress and Duration headers and the Job, Stage and time cells stay on one line and nothing scrolls sideways at 1280x800`, async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 })
    await page.emulateMedia({ colorScheme: scheme })
    await mockJobsApi(page, [...pageJobs(), ...failedTranscription()], true)
    await page.goto('/#/jobs')
    await chip(page, 'All').click()
    for (const name of ['Progress', 'Duration']) {
      const lines = await page.getByRole('columnheader', { name }).evaluate((th) => {
        const walker = document.createTreeWalker(th, NodeFilter.SHOW_TEXT)
        let count = 0
        for (let n = walker.nextNode(); n; n = walker.nextNode()) {
          const r = document.createRange()
          r.selectNodeContents(n)
          count += new Set([...r.getClientRects()].map((x) => Math.round(x.top))).size
        }
        return count
      })
      expect(lines, `${name} header wraps`).toBe(1)
    }
    // A cell that wraps is taller than one line: "Translate" used to split as "Translat" / "e".
    const wrapped = await page.getByTestId('jobs-table').locator('tbody td.jobs-name strong, tbody td.jobs-nowrap').evaluateAll((cells) =>
      cells.filter((c) => {
        const r = document.createRange()
        r.selectNodeContents(c)
        return new Set([...r.getClientRects()].map((x) => Math.round(x.top))).size > 1
      }).map((c) => c.textContent),
    )
    expect(wrapped, 'cells that wrap').toEqual([])
    const { scroll, client } = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }))
    expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
    const table = await page.getByTestId('jobs-table').evaluate((t) => ({ right: t.getBoundingClientRect().right, scrolls: t.parentElement!.scrollWidth > t.parentElement!.clientWidth }))
    expect(table.right).toBeLessThanOrEqual(1280)
    expect(table.scrolls, 'the table needs its own horizontal scroll').toBe(false)
    if (process.env.JOBS_SCREENS_DIR) await page.screenshot({ path: `${process.env.JOBS_SCREENS_DIR}/jobs-desktop-${scheme}.png`, fullPage: true })
  })
}

test('a long job name wraps inside the Job column and never runs into Title', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  const t = Math.floor(Date.now() / 1000)
  const long = { progress: null, message: '', error: null, gpu_touching: false, updated_at: t, owned_by_me: true, page: null, kind: 'transcribe', drama_id: 3, job_id: 'transcribe_compare_3', status: 'done', description: 'Comparing transcription of 183 line(s) (title #3)', started_at: t - 120, finished_at: t - 50, outcome: 'ok', outcome_message: 'Finished.' }
  await mockJobsApi(page, [...pageJobs(), long], true)
  await page.goto('/#/jobs')
  await chip(page, 'All').click()
  const row = page.getByTestId('job-row-transcribe_compare_3')
  const name = await row.locator('td.jobs-name strong').evaluate((el) => el.getBoundingClientRect().right)
  const title = await row.locator('td.jobs-title').evaluate((el) => el.getBoundingClientRect().left)
  expect(name, 'job name reaches into the Title column').toBeLessThanOrEqual(title)
  const cellRight = await row.locator('td.jobs-name').evaluate((el) => el.getBoundingClientRect().right)
  expect(name).toBeLessThanOrEqual(cellRight)
})
