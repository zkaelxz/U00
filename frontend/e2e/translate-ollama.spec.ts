import { expect, test, type Page } from '@playwright/test'

// Parity X24. The config read passes the real seeded config through with the
// drama's saved engine set to Ollama and the reachability flag the test
// controls. Nothing is translated and no Ollama server is contacted.

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

type Engine = { name: string; label: string; key_configured: boolean; models: string[] }

async function mockConfig(page: Page, state: { engine: string; reachable: boolean | null }) {
  await page.route('**/api/translate-run/dramas/1/config', async (route) => {
    const real = await (await route.fetch()).json()
    const engines: Engine[] = real.engines.some((e: Engine) => e.name === 'ollama')
      ? real.engines
      : [...real.engines, { name: 'ollama', label: 'Ollama', key_configured: true, models: [] }]
    await route.fulfill({
      json: {
        ...real,
        engines,
        translation_engine: state.engine,
        ollama_reachable: state.reachable,
        line_count: 3,
        untranslated_count: 3,
      },
    })
  })
}

test('warns when Ollama is unreachable, keeps Translate enabled, and Check again clears it', async ({ page }) => {
  const state = { engine: 'ollama', reachable: false as boolean | null }
  await mockConfig(page, state)
  await page.goto('/#/drama/1/translate')
  const run = page.getByRole('region', { name: 'Translate run' })

  const warning = run.getByTestId('ollama-warning')
  await expect(warning).toContainText("Can't reach Ollama on this PC. Is it running? Start Ollama, then check again.")
  // The server never sends a URL, and the page never builds one.
  await expect(warning).not.toContainText(/https?:|localhost|127\.0\.0\.1|11434/)
  // A warning, not a block: Translate stays pressable.
  await expect(run.getByRole('button', { name: /^Translate \d+ lines?$/ })).toBeEnabled()

  // An unsaved form edit survives the re-check.
  await run.getByText('Advanced', { exact: true }).click()
  await run.getByLabel('Batch size', { exact: true }).fill('17')

  // Still down: the warning stays and says it checked.
  await warning.getByRole('button', { name: 'Check again' }).click()
  await expect(warning).toContainText('Still no answer.')

  // Back up: the warning goes away.
  state.reachable = true
  await warning.getByRole('button', { name: 'Check again' }).click()
  await expect(warning).toBeHidden()
  await expect(run.getByLabel('Batch size', { exact: true })).toHaveValue('17')
})

test('no warning when Ollama is only picked in the form (not checked yet)', async ({ page }) => {
  const config = await (await page.request.get('/api/translate-run/dramas/1/config')).json()
  const saved = config.translation_engine === 'ollama' ? 'claude' : config.translation_engine
  await mockConfig(page, { engine: saved, reachable: null })
  await page.goto('/#/drama/1/translate')
  const run = page.getByRole('region', { name: 'Translate run' })

  await run.getByLabel('AI engine', { exact: true }).selectOption('ollama')
  await expect(run.getByRole('button', { name: /^Translate \d+ lines?$/ })).toBeEnabled()
  await expect(run.getByTestId('ollama-warning')).toHaveCount(0)
})
