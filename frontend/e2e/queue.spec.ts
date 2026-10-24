import { expect, test } from './fixtures'
import { mockJobsApi } from './jobsMenuMocks'
import { EXPORT_FIELDS, TRANSCRIBE_FIELDS, TRANSLATE_FIELDS, mockQueueApi, queueItem } from './queueMocks'

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

test('All steps adds Transcribe, Translate and Export in one call and starts nothing', async ({ page }) => {
  await mockJobsApi(page, [], true)
  const mock = await mockQueueApi(page)
  await page.goto('/#/jobs')
  const card = page.getByRole('region', { name: 'Queue' })
  await card.getByLabel('Title').selectOption({ label: 'Signal' })
  await card.getByLabel('Step').selectOption('all')
  await expect(card.getByTestId('queue-step-help')).toContainText('Nothing starts until you press Start queue.')
  await expect(card.getByTestId('queue-step-help')).toContainText('Uses Claude. Uses your API key and costs money.')
  await card.getByRole('button', { name: 'Add to queue' }).click()
  await expect(items(page)).toHaveCount(3)
  await expect(items(page).nth(0)).toContainText('Transcribe')
  await expect(items(page).nth(1)).toContainText('Translate')
  await expect(items(page).nth(2)).toContainText('Export subtitles')
  await expect(items(page).nth(0)).toContainText('Waiting')
  expect(mock.calls).toEqual(['/items/all-steps'])
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
  await page.getByRole('button', { name: 'Confirm remove' }).click()
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

test('an empty queue has no Start button until a step is waiting', async ({ page }) => {
  await mockJobsApi(page, [], true)
  await mockQueueApi(page)
  await page.goto('/#/jobs')
  const card = page.getByRole('region', { name: 'Queue' })
  await expect(card.getByTestId('queue-empty')).toBeVisible()
  await expect(card.getByRole('button', { name: 'Start queue now' })).toHaveCount(0)
})

test('a member sees no run controls', async ({ page }) => {
  await mockJobsApi(page, [], true)
  await mockQueueApi(page, [queueItem(1)], { can_control: false })
  await page.goto('/#/jobs')
  const card = page.getByRole('region', { name: 'Queue' })
  await expect(card).toContainText('Only the PC owner can start, pause or schedule the queue.')
  await expect(card.getByRole('button', { name: 'Start queue now' })).toHaveCount(0)
})

test('Saved settings for this step: closed, read-only, per step, with a link to the stage that changes them', async ({ page }) => {
  await mockJobsApi(page, [], true)
  const mock = await mockQueueApi(page)
  await page.goto('/#/jobs')
  const card = page.getByRole('region', { name: 'Queue' })
  // Nothing to show until a title is picked, and nothing is read while it is closed.
  await expect(card.getByText('Saved settings for this step')).toHaveCount(0)
  await card.getByLabel('Title').selectOption({ label: 'Signal' })
  const panel = card.locator('details.section').filter({ hasText: 'Saved settings for this step' })
  await expect(panel).not.toHaveAttribute('open', '')
  expect(mock.settingsReads).toEqual([])

  await panel.locator('summary').click()
  const rows = panel.getByTestId('queue-saved-settings')
  await expect(rows).toContainText('Speech model')
  await expect(rows).toContainText('Whisper small')
  await expect(panel.getByRole('link', { name: "Change in the title's Media stage" })).toHaveAttribute('href', '#/drama/3/source')

  await card.getByLabel('Step').selectOption('translate')
  await expect(rows).toContainText('Claude, claude-sonnet-5-5')
  for (const label of ['Tone and character voice', 'Batch size', 'Thinking', 'Glossary', 'Language packs', 'VTuber names', 'Reply check', 'Off-peak start', 'Cost', 'Monthly cap left']) {
    await expect(rows).toContainText(label)
  }
  await expect(panel.getByRole('link', { name: "Change in the title's Translate stage" })).toHaveAttribute('href', '#/drama/3/translate')
  // Read-only: no inputs, and reading it queued nothing.
  await expect(panel.locator('input, select, textarea')).toHaveCount(0)
  expect(mock.calls).toEqual([])
  expect(mock.settingsReads).toEqual(['3:transcribe', '3:translate'])
  // One full stop, not two, in the sentence under the form.
  await expect(card.getByTestId('queue-step-help')).not.toContainText('..')
})

test('a Transcribe step starts at the title\'s saved settings and sends only what was changed', async ({ page }) => {
  await mockJobsApi(page, [], true)
  const mock = await mockQueueApi(page)
  mock.fields.transcribe = TRANSCRIBE_FIELDS
  await page.goto('/#/jobs')
  const card = page.getByRole('region', { name: 'Queue' })
  await card.getByLabel('Title').selectOption({ label: 'Signal' })
  const panel = card.locator('details.section').filter({ hasText: 'Saved settings for this step' })
  await panel.locator('summary').click()
  const edit = panel.getByTestId('queue-edit-settings')
  await expect(edit.getByRole('combobox', { name: 'Whisper model' })).toHaveValue('small')
  await expect(edit.getByRole('spinbutton', { name: 'Beam size' })).toHaveValue('5')
  await expect(edit.getByRole('switch', { name: 'Groq (paid)' })).toBeDisabled()
  await expect(edit).toContainText('Needs paid-engine permission.')

  await edit.getByRole('spinbutton', { name: 'Beam size' }).fill('8')
  await edit.getByRole('switch', { name: 'Separate vocals first' }).click()
  await expect(edit).toContainText('2 changes for this step')
  await card.getByRole('button', { name: 'Add to queue' }).click()
  await expect(items(page)).toHaveCount(1)
  expect(mock.addBodies).toEqual([{ drama_id: 3, step: 'transcribe', settings: { beam_size: 8, separate_vocals_first: true } }])
  await expect(items(page).nth(0)).toContainText('Changed for this step')
  // The next add starts from the saved settings again.
  await expect(edit.getByRole('spinbutton', { name: 'Beam size' })).toHaveValue('5')
})

test('a Transcribe step left as saved sends no settings', async ({ page }) => {
  await mockJobsApi(page, [], true)
  const mock = await mockQueueApi(page)
  mock.fields.transcribe = TRANSCRIBE_FIELDS
  await page.goto('/#/jobs')
  const card = page.getByRole('region', { name: 'Queue' })
  await card.getByLabel('Title').selectOption({ label: 'Signal' })
  await card.locator('details.section').filter({ hasText: 'Saved settings for this step' }).locator('summary').click()
  await card.getByRole('button', { name: 'Add to queue' }).click()
  await expect(items(page)).toHaveCount(1)
  expect(mock.addBodies).toEqual([{ drama_id: 3, step: 'transcribe' }])
})

test('Translate and Export steps start at the saved settings and send only what was changed', async ({ page }) => {
  await mockJobsApi(page, [], true)
  const mock = await mockQueueApi(page)
  mock.fields.translate = TRANSLATE_FIELDS
  mock.fields.export = EXPORT_FIELDS
  await page.goto('/#/jobs')
  const card = page.getByRole('region', { name: 'Queue' })
  await card.getByLabel('Title').selectOption({ label: 'Signal' })
  const panel = card.locator('details.section').filter({ hasText: 'Saved settings for this step' })
  await panel.locator('summary').click()

  await card.getByLabel('Step', { exact: true }).selectOption('translate')
  const edit = panel.getByTestId('queue-edit-settings')
  await expect(edit.getByRole('spinbutton', { name: 'Batch size' })).toHaveValue('20')
  // The cost line and the cap stay readable; the rows a control edits are not repeated.
  const rows = panel.getByTestId('queue-saved-settings')
  await expect(rows).toContainText('Cost')
  await expect(rows).toContainText('Monthly cap left')
  await expect(rows).not.toContainText('Batch size')
  // A changed batch size re-reads the rows so the cost line prices it.
  const repriced = page.waitForRequest((r) => r.url().includes('/api/queue/settings') && r.url().includes('batch_size=30'))
  await edit.getByRole('spinbutton', { name: 'Batch size' }).fill('30')
  await repriced
  await edit.getByRole('combobox', { name: 'English variant' }).selectOption('en-GB')
  await expect(edit).toContainText('2 changes for this step')
  await card.getByRole('button', { name: 'Add to queue' }).click()

  await card.getByLabel('Step', { exact: true }).selectOption('export')
  await edit.getByRole('combobox', { name: 'Format' }).selectOption('vtt')
  await card.getByRole('button', { name: 'Add to queue' }).click()
  await expect(items(page)).toHaveCount(2)
  expect(mock.addBodies).toEqual([
    { drama_id: 3, step: 'translate', settings: { batch_size: 30, locale: 'en-GB' } },
    { drama_id: 3, step: 'export', settings: { format: 'vtt' } },
  ])
})

test('a folded Section shows a marker that turns when it opens', async ({ page }) => {
  await mockJobsApi(page, [], true)
  await mockQueueApi(page)
  await page.goto('/#/jobs')
  const summary = page.getByRole('region', { name: 'Queue' }).locator('details.section > summary', { hasText: 'How the queue works' })
  const marker = () => summary.evaluate((el) => getComputedStyle(el, '::before').content)
  const closed = await marker()
  expect(closed).not.toBe('none')
  await summary.click()
  await expect(summary.locator('..')).toHaveAttribute('open', '')
  expect(await marker()).not.toBe(closed)
})

test('the Queue card does not poll while the tab is hidden', async ({ page }) => {
  await page.clock.install()
  await mockJobsApi(page, [], true)
  await mockQueueApi(page, [queueItem(1, { title: 'Signal' })])
  let reads = 0
  page.on('request', (r) => {
    if (r.method() === 'GET' && new URL(r.url()).pathname === '/api/queue') reads += 1
  })
  await page.goto('/#/jobs')
  await expect(items(page)).toHaveCount(1)
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true })
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' })
    document.dispatchEvent(new Event('visibilitychange'))
  })
  const before = reads
  await page.clock.runFor(65_000)
  // Request events arrive after the fake clock's ticks: give them a moment.
  await new Promise((r) => setTimeout(r, 1000))
  expect(reads).toBe(before)
})
