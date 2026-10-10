import { expect, test } from './fixtures'
import { mockJobsApi } from './jobsMenuMocks'
import { mockQueueApi, queueItem } from './queueMocks'

// The Queue card on the Jobs page with /api/queue mocked: add (with the cost
// line for a paid Translate), keyboard reorder, remove, start, pause, and how
// skipped, interrupted and missed show.

const items = (page: import('@playwright/test').Page) => page.getByTestId('queue-items').locator('> li')

test('add items, with the engine and cost shown for a paid Translate', async ({ page }) => {
  await mockJobsApi(page, [], true)
  const mock = await mockQueueApi(page)
  await page.goto('/#/jobs')
  const card = page.getByRole('region', { name: 'Queue' })
  await expect(card.getByTestId('queue-empty')).toBeVisible()
  await card.getByLabel('Title').selectOption({ label: 'Signal' })
  await card.getByLabel('Step').selectOption('translate')
  await expect(card.getByTestId('queue-step-help')).toContainText('Uses Claude. Uses your API key and costs money. It stops at your $5.00 monthly spending cap.')
  await card.getByRole('button', { name: 'Add to queue' }).click()
  await card.getByLabel('Title').selectOption({ label: 'Kae' })
  await card.getByLabel('Step').selectOption('export')
  await card.getByRole('button', { name: 'Add to queue' }).click()
  await expect(items(page)).toHaveCount(2)
  await expect(items(page).nth(0)).toContainText('Signal')
  await expect(items(page).nth(0)).toContainText('Uses your API key and costs money')
  await expect(items(page).nth(1)).toContainText('Export subtitles')
  expect(mock.calls).toEqual(['/items', '/items'])
})

test('reorder with the keyboard keeps focus on the moved item, then remove', async ({ page }) => {
  await mockJobsApi(page, [], true)
  await mockQueueApi(page, [
    queueItem(1, { title: 'Signal' }),
    queueItem(2, { drama_id: 4, title: 'Kae', step: 'export' }),
  ])
  await page.goto('/#/jobs')
  const up = page.getByRole('button', { name: 'Move up Export subtitles Kae' })
  await up.focus()
  await page.keyboard.press('Enter')
  await expect(items(page).nth(0)).toContainText('Kae')
  // At the top now: Move up is disabled, so focus lands on its Move down.
  await expect(page.getByRole('button', { name: 'Move down Export subtitles Kae' })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(items(page).nth(1)).toContainText('Kae')
  await expect(page.getByRole('button', { name: 'Move up Export subtitles Kae' })).toBeFocused()
  await page.getByRole('button', { name: 'Remove Transcribe Signal' }).click()
  await expect(items(page)).toHaveCount(1)
})

test('start, then pause; the policy toggle and a start time', async ({ page }) => {
  await mockJobsApi(page, [], true)
  const mock = await mockQueueApi(page, [queueItem(1), queueItem(2, { drama_id: 4, title: 'Kae' })])
  await page.goto('/#/jobs')
  const card = page.getByRole('region', { name: 'Queue' })
  await card.getByRole('button', { name: 'Start queue now' }).click()
  await expect(items(page).nth(0)).toContainText('Running')
  await expect(card).toContainText('Running: one item at a time.')
  await expect(card.getByRole('button', { name: 'Remove Transcribe Signal' })).toHaveCount(0)
  await card.getByRole('button', { name: 'Pause' }).click()
  await expect(card).toContainText('Paused: no new item starts. A running one finishes.')
  await card.getByRole('switch', { name: 'Stop the queue when an item fails' }).click()
  await expect(card.getByRole('switch', { name: 'Stop the queue when an item fails' })).toHaveAttribute('aria-checked', 'true')
  await card.getByLabel('Start at', { exact: true }).fill('02:30')
  await card.getByRole('button', { name: 'Set start time' }).click()
  await expect(card.getByTestId('queue-schedule')).toContainText('Starts once at 02:30')
  expect(mock.calls).toEqual(['/start', '/pause', '/policy', '/schedule'])
})

test('skipped and interrupted items say why and offer Retry; a missed start asks first', async ({ page }) => {
  await mockJobsApi(page, [], true)
  const mock = await mockQueueApi(page, [
    queueItem(1, { status: 'skipped', step: 'translate', reason: "Skipped: This month's spending cap ($5.00) is already used up." }),
    queueItem(2, { drama_id: 4, title: 'Kae', status: 'interrupted', reason: 'Interrupted: Baihe stopped while this ran. Press Retry to run it again.' }),
    queueItem(3, { status: 'done', step: 'export' }),
  ], { schedule: { time: '02:00', start_at: Date.now() / 1000 - 3600, missed: true } })
  await page.goto('/#/jobs')
  await expect(items(page).nth(0)).toContainText('Skipped')
  await expect(items(page).nth(0)).toContainText('spending cap ($5.00) is already used up')
  await expect(items(page).nth(1)).toContainText('Interrupted')
  await expect(items(page).nth(2).getByRole('link', { name: 'Download subtitles' })).toHaveAttribute('href', '/api/artifacts/dramas/3/subtitle')
  const missed = page.getByTestId('queue-missed')
  await expect(missed).toContainText('Missed: the start at 02:00 passed while Baihe was off. Start now?')
  await page.getByRole('button', { name: 'Retry Transcribe Kae' }).click()
  await expect(items(page).nth(1)).toContainText('Waiting')
  await missed.getByRole('button', { name: 'Start now' }).click()
  await expect(page.getByTestId('queue-missed')).toHaveCount(0)
  await page.getByRole('button', { name: /^Clear finished/ }).click()
  await expect(items(page)).toHaveCount(1)
  expect(mock.calls).toEqual(['/items/2/retry', '/start', '/clear-finished'])
})

test('a member sees no run controls', async ({ page }) => {
  await mockJobsApi(page, [], true)
  await mockQueueApi(page, [queueItem(1)], { can_control: false })
  await page.goto('/#/jobs')
  const card = page.getByRole('region', { name: 'Queue' })
  await expect(card).toContainText('Only the PC owner can start, pause or schedule the queue.')
  await expect(card.getByRole('button', { name: 'Start queue now' })).toHaveCount(0)
})
