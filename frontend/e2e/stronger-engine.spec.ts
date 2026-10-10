import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test, type Page, type Route } from '@playwright/test'

// Review → "Try with a stronger engine". Drama 3 (engine: Claude,
// the column default) gets a flagged translated line; Settings' "Stronger
// translation for hard lines" is set to DeepSeek through the real route, so
// the suggestion comes from the real API. The try itself (a paid call) is
// mocked with page.route; "Use this" saves through the real line patch.

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')
const shotDir = process.env.STRONGER_SHOTS_DIR

function python(code: string): string {
  return execFileSync(process.env.PYTHON ?? 'python', ['-c', `import db\ndb.configure_library_dir(${JSON.stringify(libraryDir)})\n${code}`], {
    cwd: repoRoot,
  }).toString()
}

const CAPABILITY = '/api/settings/engine-routing/capabilities/translation.high_quality'
let ids: number[] = []

test.beforeEach(async ({ request }) => {
  const out = python(`
import json
from core import Line
db.update_drama(3, audio_filename=None)
db.save_lines(3, [
    Line(idx=0, start=0.0, end=1.5, zh='他走了', en='He left', flag='uncertain'),
    Line(idx=1, start=2.0, end=3.5, zh='好的', en='Okay'),
])
print(json.dumps([r['id'] for r in db.load_lines(3)]))
`)
  ids = JSON.parse(out.trim().split('\n').pop() as string)
  expect((await request.post(CAPABILITY, { data: { engine: 'deepseek' } })).ok()).toBe(true)
})

test.afterAll(async ({ request }) => {
  await request.post(CAPABILITY, { data: { engine: null } })
  python('db.save_lines(3, [])')
})

const row = (page: Page, i: number) => page.locator(`.review-line[data-line-id="${ids[i]}"]`)

const result = (over: Record<string, unknown> = {}) => ({
  drama_id: 3, line_id: ids[0], engine: 'deepseek', model: 'deepseek-chat',
  text: 'He walked away.', based_on_en: 'He left', cost_usd: 0.0021, ...over,
})

async function openReview(page: Page) {
  await page.goto('/#/drama/3/review')
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(2)
  // The flagged line opens active.
  await expect(row(page, 0)).toHaveAttribute('aria-current', 'true')
}

test('offer on a flagged line: try, see the cost, use it', async ({ page }) => {
  let release: () => void = () => {}
  const held = new Promise<void>((r) => (release = r))
  let tries = 0
  let body: unknown = null
  await page.route('**/api/stronger-engine/dramas/3/lines/*/try', async (route: Route) => {
    tries += 1
    body = route.request().postDataJSON()
    await held
    await route.fulfill({ json: result() })
  })
  await page.setViewportSize({ width: 1440, height: 900 })
  await openReview(page)

  const offer = row(page, 0).getByTestId('line-stronger')
  await expect(offer.getByTestId('line-stronger-reasons')).toHaveText('Flagged in review')
  const tryBtn = offer.getByRole('button', { name: /^Try with DeepSeek \(/ })
  await expect(tryBtn).toBeVisible()
  // Nothing runs on its own, and an unflagged line has no offer.
  expect(tries).toBe(0)
  await expect(row(page, 1).getByTestId('line-stronger')).toHaveCount(0)
  if (shotDir) await page.screenshot({ path: `${shotDir}/stronger-desktop-offer.png` })

  await tryBtn.click()
  await expect(offer.getByRole('button', { name: 'Translating with DeepSeek…' })).toBeDisabled()
  release()
  const res = offer.getByTestId('line-stronger-result')
  await expect(res.getByTestId('line-stronger-text')).toHaveText('He walked away.')
  await expect(res).toContainText('He left')
  await expect(res.getByTestId('line-stronger-cost')).toHaveText('Cost: $0.0021')
  expect(tries).toBe(1)
  expect(body).toEqual({})
  // Still not applied.
  await expect(row(page, 0).getByTestId('line-en')).toHaveText('He left')
  if (shotDir) await page.screenshot({ path: `${shotDir}/stronger-desktop.png` })

  const save = page.waitForRequest((r) => r.method() === 'POST' && r.url().endsWith(`/lines/${ids[0]}`))
  await res.getByRole('button', { name: 'Use this' }).click()
  const sent = (await save).postDataJSON()
  expect(sent).toEqual({ en: 'He walked away.', expected: { en: 'He left' } })
  await expect(row(page, 0).getByTestId('line-en')).toHaveText('He walked away.')
  await expect(res).toHaveCount(0)
  // Saved for real.
  expect(python(`print(db.load_lines(3)[0]['en'])`).trim()).toBe('He walked away.')
})

test('a result for English that has since changed is not applied', async ({ page }) => {
  await page.route('**/api/stronger-engine/dramas/3/lines/*/try', (route) =>
    route.fulfill({ json: result({ based_on_en: 'Something older' }) }))
  await openReview(page)
  const offer = row(page, 0).getByTestId('line-stronger')
  await offer.getByRole('button', { name: /^Try with DeepSeek/ }).click()
  const res = offer.getByTestId('line-stronger-result')
  await expect(res.getByRole('alert')).toContainText('This line changed after the try')
  await expect(res.getByRole('button', { name: 'Use this' })).toBeDisabled()
  await res.getByRole('button', { name: 'Dismiss' }).click()
  await expect(res).toHaveCount(0)
  await expect(row(page, 0).getByTestId('line-en')).toHaveText('He left')
})

test('the monthly cap refusal shows the server message', async ({ page }) => {
  const msg = 'This would go over your monthly spending cap. Raise the cap in Settings to try it.'
  await page.route('**/api/stronger-engine/dramas/3/lines/*/try', (route) =>
    route.fulfill({ status: 400, json: { error: { code: 'unsupported_operation', message: msg } } }))
  await openReview(page)
  const offer = row(page, 0).getByTestId('line-stronger')
  await offer.getByRole('button', { name: /^Try with DeepSeek/ }).click()
  await expect(offer.getByTestId('line-stronger-error')).toContainText(msg)
  await expect(offer.getByRole('button', { name: /^Try with DeepSeek/ })).toBeEnabled()
})

test('no offer when the stronger engine is the one the title uses', async ({ page, request }) => {
  expect((await request.post(CAPABILITY, { data: { engine: 'claude' } })).ok()).toBe(true)
  await openReview(page)
  await expect(row(page, 0).getByTestId('line-flag')).toBeVisible()
  await expect(page.getByTestId('line-stronger')).toHaveCount(0)
})
