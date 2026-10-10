import { expect, test } from '@playwright/test'

import { withTranslateLines } from './stageLineMocks'

// Mirrors translate_engines.TRANSLATION_ONLY_ENGINES (none are offered now).
const TRANSLATION_ONLY: string[] = []

// The run and job endpoints are mocked: nothing is translated. Config,
// estimate, glossary and characters reads hit the real seeded API.

const job = (status: string, extra: object = {}) => ({
  job_id: 'tr-job', status, progress: 0.5, message: 'translating', error: null, description: null,
  gpu_touching: false, started_at: 1, finished_at: null, updated_at: 1, ...extra,
})

test('shows config, estimates, and starts a run with the chosen options', async ({ page }) => {
  const config = await (await page.request.get('/api/translate-run/dramas/1/config')).json()
  // A fallback must be the same kind as the main engine (AI or translation-only).
  const kind = (n: string) => TRANSLATION_ONLY.includes(n)
  const other = config.engines.find((e: { name: string }) =>
    e.name !== config.translation_engine && kind(e.name) === kind(config.translation_engine)).name

  const bodies: Record<string, unknown>[] = []
  let cancelled = false
  await page.route('**/api/translate-run/dramas/1/run', async (route) => {
    bodies.push(route.request().postDataJSON())
    await route.fulfill({
      json: { job_id: 'tr-job', drama_id: 1, engine: config.translation_engine, model: null, target_line_count: 0, fallback_engines: [other] },
    })
  })
  await page.route('**/api/jobs/tr-job', (route) =>
    route.fulfill({ json: cancelled ? job('cancelled', { finished_at: 2 }) : job('running') }))
  await page.route('**/api/jobs/tr-job/cancel', (route) => {
    cancelled = true
    return route.fulfill({ json: { job_id: 'tr-job', cancel_requested: true, status: 'cancelled' } })
  })

  await withTranslateLines(page)
  await page.goto('/#/drama/1/translate')
  const run = page.getByRole('region', { name: 'Translate run' })
  await expect(run.getByRole('button', { name: /^Translate \d+ lines?$/ })).toBeEnabled()
  await expect(run.getByLabel('AI engine', { exact: true })).toBeVisible()
  // Options live in a collapsed Advanced section with a summary of non-default values.
  await expect(run.getByText('Defaults', { exact: true })).toBeVisible()
  await expect(run.getByLabel('Batch size', { exact: true })).toBeHidden()

  await run.getByRole('button', { name: 'Estimate cost' }).click()
  await expect(page.getByTestId('estimate').or(page.getByRole('alert'))).toBeVisible()

  await run.getByText('More options', { exact: true }).click()

  // Out-of-range values are caught before any request.
  await run.getByLabel('Batch size', { exact: true }).fill('500')
  await run.getByRole('button', { name: /^Translate \d+ lines?$/ }).click()
  await expect(run.getByRole('alert')).toContainText('Batch size')
  expect(bodies).toEqual([])

  await run.getByLabel('Batch size', { exact: true }).fill('10')
  await run.getByLabel('Cost cap', { exact: true }).fill('2.5')
  await run.getByRole('button', { name: 'Add fallback engine' }).click()
  await run.getByLabel('Fallback engine 1').selectOption(other)
  await run.getByRole('switch', { name: 'Re-translate existing' }).click()
  // Re-translate needs its confirmation: the primary is disabled and says so.
  await expect(run.getByRole('button', { name: /^Translate \d+ lines?$/ })).toBeDisabled()
  await expect(run.getByTestId('translate-blocker')).toContainText('confirm replacing the existing English')
  expect(bodies).toEqual([])

  await run.getByLabel(/I understand this replaces/).check()
  await run.getByRole('button', { name: /^Translate \d+ lines?$/ }).click()
  await expect(page.getByTestId('job-status')).toContainText('Running')
  expect(bodies[0]).toMatchObject({
    batch_size: 10,
    job_cost_cap_usd: 2.5,
    force_retranslate: true,
    fallback_chain: [{ engine: other }],
  })
  expect('line_ids' in bodies[0]).toBe(false)

  await page.getByRole('button', { name: 'Cancel job' }).click()
  await expect(page.getByTestId('job-status')).toContainText('Cancelled')
})

test('fallback engines: the rule is shown, only same-kind engines are offered, Reflect turns them off', async ({ page }) => {
  const config = await (await page.request.get('/api/translate-run/dramas/1/config')).json()
  const names: string[] = config.engines.map((e: { name: string }) => e.name)
  const main: string = config.translation_engine
  const kind = (n: string) => TRANSLATION_ONLY.includes(n)
  const sameKind = names.filter((n) => n !== main && kind(n) === kind(main))
  const otherKind = names.find((n) => kind(n) !== kind(main))
  test.skip(!sameKind.length || !otherKind, 'the seeded config needs engines of both kinds')

  await withTranslateLines(page)
  await page.goto('/#/drama/1/translate')
  const run = page.getByRole('region', { name: 'Translate run' })
  await run.getByText('More options', { exact: true }).click()
  const group = run.getByTestId('fallback-engines')
  await expect(group).toContainText('Up to 2, tried in order only after the main engine keeps failing (it retries first).')
  await expect(group).toContainText('Not with Reflect or Bulk.')

  await group.getByRole('button', { name: 'Add fallback engine' }).click()
  const slot = group.getByLabel('Fallback engine 1')
  const offered = await slot.locator('option').evaluateAll((os) => os.map((o) => (o as HTMLOptionElement).value).filter(Boolean))
  expect(offered).toEqual(sameKind)
  await slot.selectOption(sameKind[0])

  // Switching the main engine to the other kind flags the chosen fallback.
  await run.getByLabel('AI engine', { exact: true }).selectOption(otherKind!)
  await expect(group.getByRole('alert')).toHaveText(/must be the same kind as the main engine/)
  await expect(slot.locator('option:checked')).toContainText("can't be used here")
  await group.getByRole('button', { name: 'Remove' }).click()
  await expect(group.getByRole('alert')).toHaveCount(0)

  // Back on an AI engine, Reflect turns the picker off and says why.
  if (!kind(main)) {
    await run.getByLabel('AI engine', { exact: true }).selectOption('')
    await group.getByRole('button', { name: 'Add fallback engine' }).click()
    await run.getByRole('switch', { name: 'Reflect' }).click()
    await expect(group).toContainText('Off while Reflect is on; remove these to run.')
    await expect(group.getByLabel('Fallback engine 1')).toBeDisabled()
    await expect(group.getByRole('button', { name: 'Add fallback engine' })).toHaveCount(0)
  }
})

test('a 409 on start says a translate job is already running', async ({ page }) => {
  await page.route('**/api/translate-run/dramas/1/run', (route) =>
    route.fulfill({ status: 409, json: { error: { code: 'conflict', message: 'busy' } } }))
  await withTranslateLines(page)
  await page.goto('/#/drama/1/translate')
  await page.getByRole('button', { name: /^Translate \d+ lines?$/ }).click()
  await expect(page.getByText('A translate job is already running for this drama.')).toBeVisible()
})

test('with no lines, Translate is disabled and links to Source (rule 22)', async ({ page }) => {
  await page.goto('/#/drama/1/translate')
  const run = page.getByRole('region', { name: 'Translate run' })
  await expect(run.getByRole('button', { name: 'Translate 0 lines' })).toBeDisabled()
  const blocker = run.getByTestId('translate-blocker')
  await expect(blocker).toContainText('Still needed: lines to translate.')
  await blocker.getByRole('link', { name: 'Go to Source' }).click()
  await expect(page).toHaveURL(/#\/drama\/1\/source$/)
})

test('with every line translated, the reason offers Re-translate in one tap', async ({ page }) => {
  await withTranslateLines(page, 1, 4, 0)
  await page.goto('/#/drama/1/translate')
  const run = page.getByRole('region', { name: 'Translate run' })
  await expect(run.getByRole('button', { name: 'Translate 0 lines' })).toBeDisabled()
  await expect(run.getByTestId('translate-blocker')).toContainText('All 4 lines have English.')
  await run.getByRole('button', { name: 'Re-translate existing…' }).click()
  await run.getByLabel(/I understand this replaces/).check()
  await expect(run.getByRole('button', { name: 'Translate 4 lines' })).toBeEnabled()
  await expect(run.getByTestId('translate-blocker')).toHaveCount(0)
})

test('glossary and characters panels load; a drama without a series is told it cannot hold terms (X09)', async ({ page }) => {
  await page.goto('/#/drama/1/translate')
  // Glossary starts open; Characters is a collapsed Section, open it to reach the body.
  await page.locator('details.section', { hasText: 'Characters' }).first().locator(':scope > summary').click()
  const glossary = page.getByRole('region', { name: 'Glossary' })
  await expect(glossary.getByLabel('Project instructions')).toBeVisible()
  await expect(page.getByRole('region', { name: 'Characters' })).toBeVisible()

  // Terms belong to a series: Add term waits for one, and the reason says so.
  await expect(glossary.getByTestId('series-assign')).toContainText("can't hold glossary terms")
  await expect(glossary.getByRole('button', { name: 'Add term' })).toBeDisabled()
  await expect(glossary.getByText('Still needed: a series (above).')).toBeVisible()
  await expect(glossary.getByRole('button', { name: /^Create series/ })).toBeVisible()
})

test('in a series, the term form checks required fields and shows a failed save', async ({ page }) => {
  // The drama read says series 7; the term save is mocked to fail, so nothing is written.
  const drama = await (await page.request.get('/api/library/dramas/1')).json()
  await page.route('**/api/library/dramas/1', (r) => r.fulfill({ json: { ...drama, series_id: 7 } }))
  await page.route('**/api/glossary/dramas/1/terms', (r) =>
    r.request().method() === 'GET'
      ? r.fulfill({ json: [] })
      : r.fulfill({ status: 409, json: { error: { code: 'conflict', message: 'That term already exists.' } } }))
  await page.goto('/#/drama/1/translate')
  const glossary = page.getByRole('region', { name: 'Glossary' })
  await glossary.getByRole('button', { name: 'Add a term' }).click()
  await glossary.getByRole('button', { name: 'Save term' }).click()
  await expect(glossary.getByRole('alert')).toContainText('required')
  await glossary.getByRole('textbox', { name: 'Original', exact: true }).fill('Wei')
  await glossary.getByRole('textbox', { name: 'Translation', exact: true }).fill('Wei Wuxian')
  await glossary.getByRole('button', { name: 'Save term' }).click()
  await expect(glossary.getByRole('alert')).toBeVisible()
})

test('the last run\'s failed batches show a notice; Dismiss clears it (X01)', async ({ page }) => {
  const real = await (await page.request.get('/api/translate-run/dramas/1/config')).json()
  const errors = [
    { batch_index: 0, lines: [0, 1, 2], error: 'engine timed out' },
    { batch_index: 2, lines: [8], error: 'engine timed out' },
  ]
  await page.route('**/api/translate-run/dramas/1/config', (route) =>
    route.fulfill({ json: { ...real, last_translate_errors: errors } }))
  const dismissed: string[] = []
  await page.route('**/api/translate-run/dramas/1/errors/dismiss', (route) => {
    dismissed.push(route.request().method())
    return route.fulfill({ json: { drama_id: 1, dismissed: true } })
  })

  await page.goto('/#/drama/1/translate')
  const notice = page.getByRole('region', { name: 'Failed batches' })
  await expect(notice).toContainText('2 failed batches; lines 1-3, 9 are still untranslated')
  await expect(notice.getByRole('listitem')).toHaveText(['engine timed out'])
  await notice.getByRole('button', { name: 'Dismiss notice' }).click()
  await expect(notice).toHaveCount(0)
  expect(dismissed).toEqual(['POST'])
})

test('a failed dismiss keeps the notice and shows the error (X01)', async ({ page }) => {
  const real = await (await page.request.get('/api/translate-run/dramas/1/config')).json()
  await page.route('**/api/translate-run/dramas/1/config', (route) =>
    route.fulfill({ json: { ...real, last_translate_errors: [{ batch_index: 0, lines: [4] }] } }))
  await page.route('**/api/translate-run/dramas/1/errors/dismiss', (route) =>
    route.fulfill({ status: 403, json: { error: { code: 'forbidden', message: 'You do not have permission to do that.' } } }))

  await page.goto('/#/drama/1/translate')
  const notice = page.getByRole('region', { name: 'Failed batches' })
  await expect(notice).toContainText('1 failed batch; line 5 is still untranslated')
  await notice.getByRole('button', { name: 'Dismiss notice' }).click()
  await expect(notice.getByRole('alert')).toContainText('Not allowed')
  await expect(notice.getByRole('button', { name: 'Dismiss notice' })).toBeEnabled()
})

for (const width of [1280, 390]) {
  test(`engine notes fit the closed engine select at ${width}px wide`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 })
    await withTranslateLines(page)
    await page.goto('/#/drama/1/translate')
    const run = page.getByRole('region', { name: 'Translate run' })
    const select = run.locator('select').first()
    await expect(select).toBeVisible()
    const config = await (await page.request.get('/api/translate-run/dramas/1/config')).json()
    // Free-tier Gemini swaps in a different note, so measure both notes and every offered engine.
    const overflowing = await select.evaluate((el: HTMLSelectElement) => {
      const style = getComputedStyle(el)
      const ctx = document.createElement('canvas').getContext('2d')!
      ctx.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`
      const inner = el.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight) - 24
      return Array.from(el.options)
        .map((o) => o.text)
        .filter((t) => ctx.measureText(t).width > inner)
    })
    expect(overflowing, `engines: ${config.engines.map((e: { name: string }) => e.name)}`).toEqual([])
  })
}
