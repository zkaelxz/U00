import { expect, test, type Page } from '@playwright/test'

import { ME } from './authMocks'
import { untilTestEnds } from './stageLineMocks'

// The Translate tab after the tidy: one Style control, one action row, the
// batch-size note, the fold rule and the double-click guard. Config reads hit
// the real seeded API with a few fields overridden; runs are mocked.

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

const DRAFT_KEY = 'baihe.draft.1.translate'

async function setup(page: Page, opts: { draftForm?: Record<string, unknown>; draftVersion?: number } = {}) {
  await page.route(/\/api\/auth\/me$/, (route) => route.fulfill({ json: ME.authOff }))
  await page.route('**/api/library/dramas/1', async (route) => {
    const resp = await route.fetch()
    await route.fulfill({ response: resp, json: { ...(await resp.json()), series_id: 7 } })
  })
  await page.route('**/api/characters/series/7/characters', (route) => route.fulfill({ json: [] }))
  await page.route('**/api/translate-run/dramas/1/config', (route) =>
    untilTestEnds(async () => {
      const real = await (await route.fetch()).json()
      await route.fulfill({
        json: {
          ...real, line_count: 4, untranslated_count: 4, translation_engine: 'claude',
          batch_size_defaults: { claude: { '': 60 } },
        },
      })
    }),
  )
  if (opts.draftForm) {
    const values: Record<string, unknown> = { form: opts.draftForm, baseEngine: 'claude', reviewFirst: false }
    if (opts.draftVersion !== undefined) values.v = opts.draftVersion
    await page.addInitScript(([key, v]) => {
      if (!localStorage.getItem(key)) localStorage.setItem(key, v)
    }, [DRAFT_KEY, JSON.stringify({ v: 1, values })])
  }
}

async function openMore(page: Page) {
  const summary = page.locator('summary').filter({ hasText: 'More options' })
  const open = await summary.locator('xpath=..').getAttribute('open')
  if (open === null) await summary.click()
}

test('a batch size you typed that differs from the default is named, and Use default clears it', async ({ page }) => {
  await setup(page, { draftForm: { batch_size: '30' }, draftVersion: 2 })
  await page.goto('/#/drama/1/translate')
  await openMore(page)
  const batch = page.getByLabel('Batch size', { exact: true })
  await expect(batch).toHaveValue('30')
  await expect(page.getByTestId('batch-size-note')).toHaveText(/Using 30 \(set by you\)\. Default for this engine is 60\./)
  await page.getByRole('button', { name: 'Use default' }).click()
  await expect(batch).toHaveValue('')
  await expect(batch).toHaveAttribute('placeholder', '60')
  await expect(page.getByTestId('batch-size-note')).toHaveCount(0)
})

test('a batch size remembered by an older version is dropped for the default', async ({ page }) => {
  await setup(page, { draftForm: { batch_size: '30' } })
  await page.goto('/#/drama/1/translate')
  await openMore(page)
  const batch = page.getByLabel('Batch size', { exact: true })
  await expect(batch).toHaveValue('')
  await expect(batch).toHaveAttribute('placeholder', '60')
  await expect(page.getByTestId('batch-size-note')).toHaveCount(0)
})

test('a batch size typed equal to the default is remembered as blank and shows no note', async ({ page }) => {
  await setup(page)
  await page.goto('/#/drama/1/translate')
  await openMore(page)
  const batch = page.getByLabel('Batch size', { exact: true })
  await batch.fill('60')
  await expect(page.getByTestId('batch-size-note')).toHaveCount(0)
  await expect.poll(() => page.evaluate((k) => JSON.parse(localStorage.getItem(k) ?? 'null')?.values?.form?.batch_size, DRAFT_KEY)).toBe('')
})

test('Style is one control and the action row holds the three actions', async ({ page }) => {
  await setup(page)
  await page.goto('/#/drama/1/translate')
  const run = page.getByRole('region', { name: 'Translate run' })
  await expect(run.getByLabel('Start from…')).toHaveCount(0)
  await expect(run.getByRole('button', { name: 'Apply', exact: true })).toHaveCount(0)
  await expect(run.getByLabel('Style', { exact: true }).locator('optgroup[label="Start from a tier"]')).toHaveCount(1)

  const translate = await run.getByRole('button', { name: 'Translate 4 lines' }).boundingBox()
  const estimate = await run.getByRole('button', { name: 'Estimate cost' }).boundingBox()
  const retranslate = await run.getByRole('button', { name: /Re-translate lines affected by the glossary/ }).boundingBox()
  expect(translate && estimate && retranslate).toBeTruthy()
  expect(Math.abs(translate!.y - estimate!.y)).toBeLessThan(8)
  expect(Math.abs(translate!.y - retranslate!.y)).toBeLessThan(8)

  // Review glossary lives in More options.
  await expect(run.getByRole('switch', { name: 'Review glossary before translating' })).toBeHidden()
  await openMore(page)
  await expect(run.getByRole('switch', { name: 'Review glossary before translating' })).toBeVisible()
})

test('the cards below the run start folded and a section stays as the viewer left it', async ({ page }) => {
  await setup(page)
  await page.goto('/#/drama/1/translate')
  const section = (title: string) => page.locator('details.section').filter({ has: page.locator('.section-title', { hasText: new RegExp(`^${title}$`) }) })
  for (const title of ['Glossary', 'Characters', 'More options']) await expect(section(title)).not.toHaveAttribute('open', /.*/)
  await section('Glossary').locator(':scope > summary').click()
  await page.reload()
  await expect(section('Glossary')).toHaveAttribute('open', /.*/)
  await expect(section('Characters')).not.toHaveAttribute('open', /.*/)
})

test('the reference text row is a Section like Glossary, with no panel border around it', async ({ page }) => {
  await setup(page)
  await page.goto('/#/drama/1/translate')
  const row = page.locator('details.section').filter({ has: page.locator('.section-title', { hasText: /reference/i }) })
  await expect(row).toHaveCount(1)
  await expect(row.locator('xpath=..')).not.toHaveClass(/panel/)
})

test('pressing Translate twice quickly sends one run', async ({ page }) => {
  await setup(page)
  let runs = 0
  await page.route('**/api/translate-run/dramas/1/run', async (route) => {
    runs += 1
    await new Promise((r) => setTimeout(r, 400))
    await route.fulfill({ status: 409, json: { error: { code: 'conflict', message: 'busy' } } })
  })
  await page.goto('/#/drama/1/translate')
  const button = page.getByRole('button', { name: 'Translate 4 lines' })
  await button.dblclick()
  await expect(page.getByText('A translate job is already running for this title.')).toBeVisible()
  expect(runs).toBe(1)
})

test('the Translate tab fits about one screen and has no sideways scroll at 1280x800, light and dark', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await setup(page)
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme })
    await page.goto('/#/drama/1/translate')
    await expect(page.getByRole('button', { name: 'Translate 4 lines' })).toBeVisible()
    const m = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, iw: window.innerWidth, sh: document.documentElement.scrollHeight }))
    expect(m.sw).toBeLessThanOrEqual(m.iw)
    expect(m.sh).toBeLessThanOrEqual(1000)
    await page.screenshot({ path: `test-results/translate-tidy-${scheme}.png`, fullPage: true })
  }
})
