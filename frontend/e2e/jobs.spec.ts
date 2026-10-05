import { createServer, type ServerResponse } from 'node:http'
import type { AddressInfo } from 'node:net'

import { expect, test } from './fixtures'
import { mockJobsApi, pageJobs } from './jobsMenuMocks'
import { openGear } from './settingsNav'

// The Jobs page (#/jobs) with /api/jobs mocked: table, filters, states,
// Cancel, PC-only Delete, row details and the remote-admin persona.

const rows = (page: import('@playwright/test').Page) => page.getByTestId('jobs-table').locator('tbody > tr[data-testid^="job-row-"]')
const chip = (page: import('@playwright/test').Page, name: string) => page.getByRole('group', { name: 'Status' }).getByRole('button', { name })
const error = (status: number, message: string) => ({ status, json: { error: { code: 'x', message } } })

test('running jobs first, with title and stage links, a progress bar and live duration', async ({ page }) => {
  await mockJobsApi(page, pageJobs(), true)
  await page.goto('/#/jobs')
  await expect(page.getByRole('heading', { name: 'Jobs', level: 2 })).toBeVisible()
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
  await expect(page.getByRole('heading', { name: 'Jobs', level: 2 })).toBeVisible()
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
  await rows(page).first().getByRole('button', { name: 'Cancel Translate Signal' }).click()
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

test('Delete is two-step and permanent; only finished jobs offer it', async ({ page }) => {
  const log = await mockJobsApi(page, pageJobs(), true)
  await page.goto('/#/jobs')
  await expect(rows(page).first().getByRole('button', { name: /^Delete/ })).toHaveCount(0)
  await chip(page, 'All').click()
  await expect(page.getByRole('button', { name: /^Delete Translate Signal/ })).toHaveCount(0)

  await page.getByRole('button', { name: 'Delete Transcribe Signal' }).click()
  expect(log.deleted).toEqual([])
  await page.getByRole('button', { name: 'Confirm delete Transcribe Signal' }).click()
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

test('the stream down: the page says it updates every 10 seconds', async ({ page }) => {
  await mockJobsApi(page, pageJobs(), true)
  await page.route('**/api/events?*', (route) => route.fulfill({ status: 429, json: { error: { code: 'x', message: 'no' } } }))
  await page.goto('/#/jobs')
  await expect(page.getByTestId('jobs-polling')).toHaveText('Updating every 10 seconds.')
})

const me = (permissions: string[], user: Record<string, unknown>) => ({
  auth_enabled: true, signed_in: true, sign_in_configured: true, zone: 'internet', permissions,
  user: { id: 1, email: 'owner@example.com', display_name: 'Owner', is_admin: true, is_local_owner: false, ...user },
})

test('remote admin: Cancel only their own, the note, no Delete, and Who and Mine show', async ({ page }) => {
  const log = await mockJobsApi(page, pageJobs(), true)
  await page.route('**/api/meta', (route) => route.fulfill({ json: { app: 'Baihe Studio', api_version: '0.1', environment: 'production', local: false } }))
  await page.route('**/api/auth/me', (route) => route.fulfill({ json: me(['library.read', 'jobs.cancel', 'admin.users.read'], {}) }))
  await page.goto('/#/jobs')
  await expect(rows(page)).toHaveCount(2)
  await expect(page.getByRole('button', { name: 'Cancel Translate Signal' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Cancel Dub Kae' })).toHaveCount(0)
  await expect(page.getByTestId('remote-admin-jobs-note')).toContainText('Changing other people’s items is done on the main PC.')
  await expect(page.getByRole('columnheader', { name: 'Who' })).toBeVisible()
  await expect(page.getByTestId('job-row-dub_4')).toContainText('Someone else')
  await expect(page.getByTestId('job-row-translate_3')).toContainText('You')

  await chip(page, 'All').click()
  await expect(page.getByRole('button', { name: /^Delete/ })).toHaveCount(0)
  await expect(page.getByTestId('jobs-clear')).toHaveCount(0)
  await expect(page.getByText('Deleting is PC only.')).toBeVisible()

  await page.getByLabel('Mine').check()
  await expect(rows(page)).toHaveCount(4)
  await expect(page.getByTestId('job-row-dub_4')).toHaveCount(0)
  expect(log.cancelled).toEqual([])
})

test('PC owner gets Cancel on every job', async ({ page }) => {
  await mockJobsApi(page, pageJobs(), true)
  await page.route('**/api/meta', (route) => route.fulfill({ json: { app: 'Baihe Studio', api_version: '0.1', environment: 'production', local: true } }))
  await page.route('**/api/auth/me', (route) => route.fulfill({ json: me(['library.read', 'jobs.cancel', 'admin.library', 'admin.users.read'], { is_local_owner: true }) }))
  await page.goto('/#/jobs')
  await expect(page.getByRole('button', { name: 'Cancel Dub Kae' })).toBeVisible()
  await expect(page.getByTestId('remote-admin-jobs-note')).toHaveCount(0)
})

test('the menu has Jobs with a running count, and it opens the page', async ({ page }) => {
  await mockJobsApi(page, pageJobs(), true)
  await page.goto('/#/library')
  await openGear(page)
  // The count badge is part of the link's name, so match by prefix: the rail at 1024px and up, the cogwheel menu below.
  const link = page
    .getByRole('navigation', { name: 'Main' })
    .getByRole('link', { name: /^Jobs/ })
    .or(page.getByRole('group', { name: 'Settings and tools pages' }).getByRole('link', { name: /^Jobs/ }))
  await expect(page.getByTestId(/^(gear|rail)-jobs-count$/)).toHaveText('2')
  await link.click()
  await expect(page).toHaveURL(/#\/jobs$/)
  await openGear(page)
  await expect(link).toHaveAttribute('aria-current', 'page')
})
