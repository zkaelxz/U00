import type { Page } from '@playwright/test'

import { expect, test } from './fixtures'
import { openSettingsGroups } from './settingsNav'

// The Settings notify switch and Tasks list say what is missing before the click. Readiness is faked; the
// seeded server answers "all present" otherwise.

async function readiness(page: Page, patch: Record<string, boolean>) {
  await page.route('**/api/setup/readiness', (r) => r.fulfill({ json: { ...patch } }))
}

test('Settings notify says desktop popups need plyer and that Discord and ntfy do not', async ({ page }) => {
  await readiness(page, { plyer: false })
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'Notifications and backups')
  const card = page.getByRole('region', { name: 'Notifications' })
  await expect(card.getByTestId('needs-notify_plyer')).toContainText('Discord and ntfy don\'t need it')
  await expect(card.getByRole('link', { name: 'Open Diagnostics' })).toBeVisible()
})

test('Settings Tasks names the task whose engine has no key, or whose package or server is missing', async ({ page }) => {
  await readiness(page, { anthropic: false, openai: true, ollama_reachable: false })
  const cap = (id: string, engine: string) => ({
    id, label: id, help: '', requires: 'translate', engine, default_engine: engine, is_default: false,
    engine_supported: true, choices: ['claude', 'deepseek', 'ollama'],
  })
  const eng = (engine: string, needs_key: boolean, key_configured: boolean) => ({
    engine, tags: [], needs_key, key_configured, status: key_configured ? 'untested' : 'not_configured', last_test: null,
  })
  await page.route('**/api/settings/engine-routing', (r) =>
    r.fulfill({
      json: {
        capabilities: [cap('a_claude', 'claude'), cap('b_deepseek', 'deepseek'), cap('c_ollama', 'ollama')],
        engines: [eng('claude', true, true), eng('deepseek', true, false), eng('ollama', false, true)],
      },
    }),
  )
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'Translation and keys')
  await expect(page.getByTestId('task-a_claude')).toContainText('anthropic package')
  await expect(page.getByTestId('task-b_deepseek')).toContainText('no API key')
  await expect(page.getByTestId('task-c_ollama')).toContainText("Ollama isn't answering")
})
