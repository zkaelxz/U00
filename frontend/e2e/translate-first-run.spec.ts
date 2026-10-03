import { expect, test, type Page } from '@playwright/test'

// Fresh install: no cloud key, so Quick translate lands on Ollama. The engine
// list and the translate call are mocked; nothing contacts Ollama.

const ENGINE = (name: string, key_configured: boolean, free: boolean) => ({
  name, label: `${name} engine.`, free, models: null, key_configured,
})

async function mockEngines(page: Page, claudeKey: boolean) {
  await page.route('**/api/translate/engines', (route) =>
    route.fulfill({
      json: {
        items: [ENGINE('claude', claudeKey, false), ENGINE('ollama', true, true)],
        default_engine: 'claude',
      },
    }),
  )
}

const NOTE = 'No cloud translator is set up. Quick translate is using Ollama on this PC. Start Ollama, or add a key in Settings.'

for (const [name, size] of [['desktop', { width: 1280, height: 800 }], ['phone', { width: 390, height: 800 }]] as const) {
  test.describe(name, () => {
    test.use({ viewport: size })

    test('shows the local-only note and the plain Ollama error', async ({ page }) => {
      await mockEngines(page, false)
      const message = "Ollama isn't running. Start it, or pick another translator in Settings."
      await page.route('**/api/translate', (route) =>
        route.request().method() === 'POST'
          ? route.fulfill({
              status: 503,
              json: { error: { code: 'dependency_unavailable', message, details: { reason: 'ollama_unreachable' } } },
            })
          : route.fallback(),
      )
      await page.goto('/#/translate')
      const note = page.getByTestId('local-only-note')
      await expect(note).toHaveText(NOTE)
      await expect(note.getByRole('link', { name: 'Settings' })).toHaveAttribute('href', '#/settings')
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
      const link = await note.getByRole('link', { name: 'Settings' }).boundingBox()
      expect(link && link.height >= 44).toBe(true)

      await page.getByLabel('Text to translate').fill('你好')
      await page.getByRole('button', { name: 'Translate', exact: true }).click()
      const alert = page.getByRole('alert')
      await expect(alert).toContainText(message)
      await expect(alert).not.toContainText('Something went wrong')
    })

    test('hides the note when a cloud engine has a key', async ({ page }) => {
      await mockEngines(page, true)
      await page.goto('/#/translate')
      await expect(page.getByLabel('Engine', { exact: true })).toHaveValue('claude')
      await expect(page.getByTestId('local-only-note')).toHaveCount(0)
    })
  })
}
