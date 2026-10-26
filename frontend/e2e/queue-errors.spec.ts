import { expect, test } from './fixtures'
import { mockJobsApi } from './jobsMenuMocks'
import { runClock } from './mockHelpers'
import { mockQueueApi, queueItem } from './queueMocks'

// The Queue card on the Jobs page when /api/queue fails: a failed read is
// shown, not a missing card, and clears once the queue answers again; a
// refused change shows the server's reason.

const items = (page: import('@playwright/test').Page) => page.getByTestId('queue-items').locator('> li')
const BOOM = { error: { code: 'internal', message: 'Boom.' } }
// What a 500 on a read shows (plainErrors).
const READ_FAILED = 'Could not load this. Try again.'

test('a failed first read says so, and the card appears when the queue answers', async ({ page }) => {
  await page.clock.install()
  await mockJobsApi(page, [], true)
  await mockQueueApi(page, [queueItem(1)])
  let fail = true
  await page.route((u) => u.pathname === '/api/queue', (route) =>
    fail && route.request().method() === 'GET' ? route.fulfill({ status: 500, json: BOOM }) : route.fallback())
  await page.goto('/#/jobs')
  await expect(page.getByRole('alert').filter({ hasText: READ_FAILED })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Queue' })).toHaveCount(0)

  // The idle re-read (every 30 s) brings the card back and the banner goes.
  fail = false
  await runClock(page, 30_000)
  await expect(items(page)).toHaveCount(1)
  await expect(page.getByRole('alert').filter({ hasText: READ_FAILED })).toHaveCount(0)
})

test('a failed re-read keeps the list, and its banner clears when the queue answers again', async ({ page }) => {
  await page.clock.install()
  await mockJobsApi(page, [], true)
  await mockQueueApi(page, [queueItem(1)])
  let fail = false
  await page.route((u) => u.pathname === '/api/queue', (route) =>
    fail && route.request().method() === 'GET' ? route.fulfill({ status: 500, json: BOOM }) : route.fallback())
  await page.goto('/#/jobs')
  await expect(items(page)).toHaveCount(1)

  fail = true
  await runClock(page, 30_000)
  const card = page.getByRole('region', { name: 'Queue' })
  await expect(card.getByRole('alert')).toContainText(READ_FAILED)
  await expect(items(page)).toHaveCount(1)

  fail = false
  await runClock(page, 30_000)
  await expect(card.getByRole('alert')).toHaveCount(0)
  await expect(items(page)).toHaveCount(1)
})

test('a refused add shows the server\'s reason and adds nothing', async ({ page }) => {
  await mockJobsApi(page, [], true)
  const mock = await mockQueueApi(page)
  await page.route('**/api/queue/items', (route) => route.request().method() === 'POST'
    ? route.fulfill({ status: 409, json: { error: { code: 'conflict', message: 'The queue is full (200 steps).' } } })
    : route.fallback())
  await page.goto('/#/jobs')
  const card = page.getByRole('region', { name: 'Queue' })
  await expect(card.getByTestId('queue-empty')).toBeVisible()
  await card.getByLabel('Title').selectOption({ label: 'Signal' })
  await card.getByLabel('Step').selectOption('export')
  await card.getByRole('button', { name: 'Add to queue' }).click()
  await expect(card.getByRole('alert')).toContainText('The queue is full (200 steps).')
  await expect(card.getByTestId('queue-empty')).toBeVisible()
  expect(mock.calls).toEqual([])
})
