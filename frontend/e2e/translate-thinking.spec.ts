import { expect, test, type Page } from '@playwright/test'

// The "Think harder" toggle in the Translate step. The config and estimate
// reads are mocked; the run request is captured, and nothing is translated.

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

const LABEL = 'Think harder on tricky text (slower, costs more)'

async function mockStage(page: Page, state: { engine: string; saved: boolean; run: unknown[] }) {
  await page.route('**/api/translate-run/dramas/1/config', async (route) => {
    const real = await (await route.fetch()).json()
    const engines = real.engines.some((e: { name: string }) => e.name === state.engine)
      ? real.engines
      : [...real.engines, { name: state.engine, label: state.engine, key_configured: true, models: [] }]
    await route.fulfill({
      json: {
        ...real,
        engines,
        translation_engine: state.engine,
        title_thinking: state.saved,
        thinking_switch_engines: ['deepseek', 'ollama'],
        line_count: 3,
        untranslated_count: 3,
      },
    })
  })
  await page.route('**/api/translate-run/dramas/1/estimate**', async (route) => {
    const thinking = new URL(route.request().url()).searchParams.get('thinking') === 'true'
    await route.fulfill({
      json: {
        engine: state.engine, model: null, estimated_usd: 0.12, target_line_count: 3, free: false,
        cap_applies: false, effective_cap_usd: null, monthly_refusal: false, estimate_above_cap: false,
        thinking, estimate_is_lower_bound: thinking,
      },
    })
  })
  await page.route('**/api/translate-run/dramas/1/run', async (route) => {
    state.run.push(route.request().postDataJSON())
    await route.fulfill({
      status: 409,
      json: { error: { code: 'conflict', message: 'A translation is already running for this drama.' } },
    })
  })
}

test('off by default, switchable for DeepSeek, and the estimate becomes a lower bound', async ({ page }) => {
  const state = { engine: 'deepseek', saved: false, run: [] as unknown[] }
  await mockStage(page, state)
  await page.goto('/#/drama/1/translate')
  const run = page.getByRole('region', { name: 'Translate run' })
  await run.getByText('Advanced', { exact: true }).click()

  const toggle = run.getByRole('switch', { name: LABEL })
  await expect(toggle).not.toBeChecked()
  await expect(toggle).toBeEnabled()
  await run.getByRole('button', { name: 'Estimate cost' }).click()
  await expect(run.getByTestId('estimate')).toContainText('about $0.12')

  await toggle.check()
  await run.getByRole('button', { name: 'Estimate cost' }).click()
  await expect(run.getByTestId('estimate')).toContainText('at least $0.12')
  await expect(run.getByTestId('estimate')).toContainText('real cost is higher')

  await run.getByRole('button', { name: /^Translate \d+ lines?$/ }).click()
  await expect.poll(() => state.run.length).toBe(1)
  expect(state.run[0]).toMatchObject({ thinking: true })
})

test('the title remembers the choice, and an engine without a switch says it does nothing', async ({ page }) => {
  const state = { engine: 'deepseek', saved: true, run: [] as unknown[] }
  await mockStage(page, state)
  await page.goto('/#/drama/1/translate')
  const run = page.getByRole('region', { name: 'Translate run' })
  await run.getByText('Advanced', { exact: true }).click()
  await expect(run.getByRole('switch', { name: LABEL })).toBeChecked()

  await run.getByLabel('AI engine', { exact: true }).selectOption('claude')
  await expect(run.getByRole('switch', { name: LABEL })).toBeDisabled()
  await expect(run.getByRole('switch', { name: LABEL })).not.toBeChecked()
  await run.getByRole('button', { name: `Help: ${LABEL}` }).click()
  await expect(run.getByText(/claude has no thinking switch, so this does nothing/)).toBeVisible()
})

test('a thinking fallback makes the switch apply, and the help names it', async ({ page }) => {
  const state = { engine: 'claude', saved: true, run: [] as unknown[] }
  await mockStage(page, state)
  await page.goto('/#/drama/1/translate')
  const run = page.getByRole('region', { name: 'Translate run' })
  await run.getByText('Advanced', { exact: true }).click()
  await expect(run.getByRole('switch', { name: LABEL })).toBeDisabled()

  await run.getByRole('button', { name: 'Add fallback engine' }).click()
  await run.getByLabel('Fallback engine 1').selectOption('deepseek')
  const toggle = run.getByRole('switch', { name: LABEL })
  await expect(toggle).toBeEnabled()
  await expect(toggle).toBeChecked()
  await run.getByRole('button', { name: `Help: ${LABEL}` }).click()
  await expect(run.getByText(/It applies to deepseek, not to the other engines in the chain/)).toBeVisible()
})
