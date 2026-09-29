import { expect, test, type Page } from '@playwright/test'

import { clearReviewResults, seedReviewResults } from './reviewResultsSeed'

// Review stage results and checks against the real API: stored consistency
// and emotion results, coverage/pacing, tendencies, version compare, the
// notes link, per-line provenance, and the fix-flagged start options (the
// job start itself is mocked).

test.beforeEach(() => seedReviewResults())
test.afterAll(() => clearReviewResults())

const rows = (page: Page) => page.locator('.review-line:not(.review-skeleton)')
const section = (page: Page, title: string) =>
  page.locator('details.section').filter({ has: page.locator('summary .section-title', { hasText: new RegExp(`^${title}$`) }) })

async function open(page: Page, title: string) {
  const s = section(page, title)
  await s.locator('summary').click()
  await expect(s).toHaveAttribute('open', '')
  return s
}

test('consistency lines and emotion tags open their line in the editor', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  await expect(rows(page)).toHaveCount(4)

  const c = await open(page, 'Consistency')
  await expect(c).toContainText('魏婴')
  await expect(c).toContainText('Wei Ying / Wei Wuxian')
  await c.getByRole('button', { name: 'Show lines' }).click()
  await expect(c.locator('.review-findings button')).toHaveText(['#2', '#3'])
  await c.getByRole('button', { name: '#3' }).click()
  await expect(rows(page).nth(2)).toHaveAttribute('aria-current', 'true')

  const e = await open(page, 'Emotion')
  await expect(e.getByTestId('emotion-list').locator('li').first()).toContainText('anger')
  await e.getByRole('button', { name: '#2' }).click()
  await expect(rows(page).nth(1)).toHaveAttribute('aria-current', 'true')
})

test('coverage, pacing, tendencies, version compare and the notes link', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  await expect(rows(page)).toHaveCount(4)

  const cov = await open(page, 'Coverage and pacing')
  await expect(cov).toContainText('17.0s for 2 characters')
  await expect(cov.getByTestId('pacing-list')).toContainText('Too long for its time slot')
  await cov.getByTestId('pacing-list').getByRole('button', { name: '#1' }).click()
  await expect(rows(page).nth(0)).toHaveAttribute('aria-current', 'true')

  const t = await open(page, 'Edit tendencies')
  await expect(t.getByTestId('tendency-stats')).toContainText('1 of your edits: 0 shortened, 1 expanded')

  const v = await open(page, 'Compare versions')
  await v.getByRole('button', { name: 'Show differences' }).click()
  await expect(v.getByTestId('compare-count')).toHaveText('1 of 4 lines differ.')
  await expect(v.getByTestId('compare-list')).toContainText('Wei Ying arrives')

  const n = await open(page, 'Notes export')
  await expect(n.getByTestId('notes-markdown')).toHaveAttribute('href', /\/api\/review\/dramas\/3\/notes\/markdown$/)
  const md = await page.request.get('/api/review/dramas/3/notes/markdown')
  expect(await md.text()).toContain('A plain greeting.')
})

test('line details show where the line came from', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  const row = rows(page).nth(0)
  await row.locator('.review-idx').click()
  await row.getByRole('button', { name: 'Edit details' }).click()
  const origin = row.getByTestId('line-origin')
  await origin.locator('summary').click()
  await expect(origin.getByTestId('original-text')).toHaveText('No raw transcription saved for this project.')
  await expect(origin).toContainText('Engine')
  await expect(origin).toContainText('gemini · m2')
  await expect(origin).toContainText('calm')
})

test('fix flagged lines sends only the options that were set', async ({ page }) => {
  const bodies: unknown[] = []
  await page.route('**/api/review-jobs/dramas/3/fix-flagged', async (route) => {
    bodies.push(route.request().postDataJSON())
    await route.fulfill({ json: { job_id: 'fx', drama_id: 3, kind: 'fix-flagged', engine: 'gemini', model: null, line_count: 1 } })
  })
  await page.route('**/api/jobs/fx', (route) => route.fulfill({ json: {
    job_id: 'fx', status: 'done', progress: null, message: '', error: null, description: null,
    gpu_touching: false, started_at: 1, finished_at: 2, updated_at: 1,
  } }))
  await page.goto('/#/drama/3/review')
  const ai = await open(page, 'AI review')
  const fix = ai.getByRole('group', { name: 'Fix flagged lines' })

  await fix.getByRole('spinbutton', { name: 'Cost cap' }).fill('-1')
  await fix.getByRole('button', { name: 'Fix flagged lines' }).click()
  await expect(fix.getByRole('alert')).toHaveText(/0 or more/)
  expect(bodies).toHaveLength(0)

  await fix.getByRole('spinbutton', { name: 'Cost cap' }).fill('0.5')
  await fix.getByRole('button', { name: 'Fix flagged lines' }).click()
  await expect(page.getByTestId('job-panel')).toBeVisible()
  expect(bodies).toEqual([{ job_cost_cap_usd: 0.5 }])
})

// Screenshots for review only (REVIEW_SHOTS=<dir>): desktop, light and dark.
test('screenshots', async ({ page }) => {
  const dir = process.env.REVIEW_SHOTS
  test.skip(!dir, 'REVIEW_SHOTS not set')
  await page.setViewportSize({ width: 1280, height: 800 })
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme })
    await page.goto('about:blank')
    await page.goto('/#/drama/3/review')
    await expect(rows(page)).toHaveCount(4)
    // Sections opened in the first pass stay open (remembered) in the second.
    if (scheme === 'light') {
      for (const t of ['AI review', 'Consistency', 'Emotion', 'Coverage and pacing', 'Compare versions']) await open(page, t)
    }
    await section(page, 'Consistency').getByRole('button', { name: 'Show lines' }).click()
    await section(page, 'Compare versions').getByRole('button', { name: 'Show differences' }).click()
    await section(page, 'AI review').scrollIntoViewIfNeeded()
    await page.screenshot({ path: `${dir}/desktop-${scheme}-results.png`, fullPage: true })
    const row = rows(page).nth(0)
    await row.locator('.review-idx').click()
    await row.getByRole('button', { name: 'Edit details' }).click()
    await row.getByTestId('line-origin').locator('summary').click()
    await expect(row.getByTestId('original-text')).toBeVisible()
    await row.screenshot({ path: `${dir}/desktop-${scheme}-line-origin.png` })
  }
})
