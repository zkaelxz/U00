import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test, type Page } from '@playwright/test'

// The engine and model picker on the per-line AI panels (Improve translation,
// Why this?, Alternatives, Grammar). The run config is the real one with the
// keys faked; the AI answers are mocked.

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')

function python(code: string) {
  execFileSync(process.env.PYTHON ?? 'python', ['-c', `import db\ndb.configure_library_dir(${JSON.stringify(libraryDir)})\n${code}`], { cwd: repoRoot })
}

test.beforeEach(() => {
  python(`
from core import Line
db.update_drama(3, audio_filename=None, source_video_filename=None)
db.save_lines(3, [
    Line(idx=0, start=0.0, end=1.5, zh='你好', en='Hello there'),
    Line(idx=1, start=1.5, end=3.0, zh='谢谢', en='Thanks, friend'),
])
`)
})

test.afterAll(() => python('db.save_lines(3, [])'))

const rows = (page: Page) => page.locator('.review-line:not(.review-skeleton)')

// Claude and Ollama get a key; DeepSeek is left without one.
async function engineConfig(page: Page) {
  await page.route('**/api/translate-run/dramas/3/config', async (route) => {
    const res = await route.fetch()
    const cfg = await res.json()
    for (const e of cfg.engines) e.key_configured = e.name !== 'deepseek'
    await route.fulfill({ response: res, json: cfg })
  })
}

async function openAi(page: Page, item: string) {
  await page.goto('/#/drama/3/review')
  await expect(rows(page)).toHaveCount(2)
  const row = rows(page).nth(0)
  await row.locator('.review-idx').click()
  await expect(row).toHaveAttribute('aria-current', 'true')
  await row.getByRole('button', { name: item, exact: true }).click()
  return row
}

const lineId = (url: string) => Number(url.split('/lines/')[1].split('/')[0])

test('the pick is sent, remembered, and unusable engines say why', async ({ page }) => {
  await engineConfig(page)
  const bodies: Record<string, unknown>[] = []
  await page.route('**/api/line-ai/dramas/3/lines/*/improve', async (route) => {
    bodies.push(route.request().postDataJSON())
    await route.fulfill({ json: {
      line_id: lineId(route.request().url()), current_en: 'Hello there', suggestion: 'Hi there', changed: true, engine: 'x', model: null,
    } })
  })
  let row = await openAi(page, 'Improve translation')
  const engine = row.getByTestId('line-ai-picker').getByLabel('AI engine', { exact: true })
  await expect(engine.locator('option', { hasText: 'Default (' })).toHaveCount(1)
  await expect(engine.locator('option[value="deepseek"]')).toBeDisabled()
  await expect(engine.locator('option[value="deepseek"]')).toContainText('no key')
  await expect(engine.locator('option[value="claude"]')).toContainText('paid')
  await row.getByRole('button', { name: 'Suggest' }).click()
  await expect(row.getByTestId('line-ai-suggestion')).toBeVisible()
  expect(bodies[0]).not.toHaveProperty('engine')

  await engine.selectOption('claude')
  await expect(row.getByTestId('line-ai-engine-hint')).toContainText('Paid')
  await row.getByRole('button', { name: 'Ask again' }).click()
  await expect(row.getByTestId('line-ai-suggestion')).toBeVisible()
  expect(bodies[1]).toMatchObject({ engine: 'claude' })

  await page.reload()
  await expect(rows(page)).toHaveCount(2)
  row = await openAi(page, 'Improve translation')
  await expect(row.getByTestId('line-ai-picker').getByLabel('AI engine', { exact: true })).toHaveValue('claude')
})

test('Why this? asks with the remembered pick; a changed pick waits for Ask again', async ({ page }) => {
  await engineConfig(page)
  await page.addInitScript(() => localStorage.setItem('baihe.review.lineAiPick', JSON.stringify({ engine: 'ollama', model: '' })))
  const bodies: Record<string, unknown>[] = []
  await page.route('**/api/line-ai/dramas/3/lines/*/explain', async (route) => {
    bodies.push(route.request().postDataJSON())
    await route.fulfill({ json: { line_id: lineId(route.request().url()), explanation: 'Friendly.', engine: 'x', model: null } })
  })
  const row = await openAi(page, 'Why this?')
  await expect(row.getByTestId('line-ai-explanation')).toHaveText('Friendly.')
  expect(bodies).toEqual([{ engine: 'ollama' }])

  await row.getByTestId('line-ai-picker').getByLabel('AI engine', { exact: true }).selectOption('claude')
  expect(bodies).toHaveLength(1)
  await row.getByTestId('line-ai-ask-again').click()
  await expect.poll(() => bodies.length).toBe(2)
  expect(bodies[1]).toEqual({ engine: 'claude' })
})

test('Alternatives sends the pick too', async ({ page }) => {
  await engineConfig(page)
  await page.addInitScript(() => localStorage.setItem('baihe.review.lineAiPick', JSON.stringify({ engine: 'claude', model: '' })))
  let body: unknown = null
  await page.route('**/api/line-ai/dramas/3/lines/*/alternatives', async (route) => {
    body = route.request().postDataJSON()
    await route.fulfill({ json: {
      line_id: lineId(route.request().url()), current_en: 'Hello there', engine: 'x', model: null,
      alternatives: [{ translation: 'Hi', approach: 'short', tradeoff: 'warmth' }],
    } })
  })
  await page.goto('/#/drama/3/review')
  await expect(rows(page)).toHaveCount(2)
  const row = rows(page).nth(0)
  await row.locator('.review-idx').click()
  await row.getByRole('button', { name: 'More actions for line 1' }).click()
  await page.getByRole('button', { name: 'Alternatives (AI)' }).click()
  await expect(row.getByTestId('line-alternatives')).toBeVisible()
  expect(body).toEqual({ engine: 'claude' })
})
