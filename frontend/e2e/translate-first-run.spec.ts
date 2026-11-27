import { expect, test, type Page } from '@playwright/test'

// Text on Ollama with no cloud key: the engine list and the translate call are
// mocked; nothing contacts Ollama.

const ENGINE = (name: string, key_configured: boolean, free: boolean) => ({
  name, label: `${name} engine.`, free, models: null, key_configured,
})

async function mockEngines(page: Page, claudeKey: boolean) {
  await page.route('**/api/translate/engines', (route) =>
    route.fulfill({
      json: {
        items: [ENGINE('claude', claudeKey, false), ENGINE('ollama', true, false)],
        default_engine: 'claude',
      },
    }),
  )
}

for (const [name, size] of [['desktop', { width: 1280, height: 800 }], ['phone', { width: 390, height: 800 }]] as const) {
  test.describe(name, () => {
    test.use({ viewport: size })

    test('shows the plain Ollama error', async ({ page }) => {
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
      await page.getByLabel('Text to translate').fill('你好')
      await page.getByRole('button', { name: 'Translate', exact: true }).click()
      const alert = page.getByRole('alert')
      await expect(alert).toContainText(message)
      await expect(alert).not.toContainText('Something went wrong')
    })
  })
}
