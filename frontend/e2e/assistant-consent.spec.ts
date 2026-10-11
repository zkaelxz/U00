import { expect, test } from '@playwright/test'

import { mockAssistant } from './assistantMocks'
import { openSettingsGroups } from './settingsNav'

// A cloud engine needs the owner's per-provider OK to receive code and logs.

test('a cloud engine without consent is refused with a plain message, then allowed from the Engine section', async ({ page }) => {
  const s = await mockAssistant(page, { developerMode: true, cloudConsent: { claude: false, gemini: false } })
  await page.goto('/#/assistant')
  const chat = page.getByRole('region', { name: 'Ask the assistant' })
  await expect(chat.getByLabel('AI engine', { exact: true }).locator('option').first()).toHaveText('Default (Ollama)')
  await chat.getByLabel('AI engine', { exact: true }).selectOption('claude')
  await chat.getByRole('textbox', { name: 'Question' }).fill('Why?')
  await chat.getByRole('button', { name: 'Ask', exact: true }).click()
  await expect(chat.getByRole('alert').first()).toContainText("isn't allowed yet")
  // The page did not fall back to the Developer Mode switch (that 409 is different).
  await expect(page.getByRole('link', { name: 'Turn on in Settings' })).toHaveCount(0)
  await chat.getByRole('switch', { name: 'Send code and logs to Claude' }).click()
  await expect(chat.getByRole('switch', { name: 'Send code and logs to Claude' })).toHaveAttribute('aria-checked', 'true')
  expect(s.calls.filter((c) => c.method === 'POST' && c.path === '/api/assistant/settings').map((c) => c.body)).toContainEqual({ cloud_consent: { claude: true } })
  await chat.getByRole('textbox', { name: 'Question' }).fill('Why now?')
  await chat.getByRole('button', { name: 'Ask', exact: true }).click()
  await expect(chat.getByTestId('assistant-answer')).toBeVisible()
})

test('Settings lists every cloud engine with its own consent switch', async ({ page }) => {
  await mockAssistant(page, { developerMode: true, cloudConsent: { claude: false, gemini: true } })
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'System')
  const card = page.getByRole('region', { name: 'Developer Mode' })
  await expect(card.getByRole('switch', { name: 'Send code and logs to Claude' })).toHaveAttribute('aria-checked', 'false')
  await expect(card.getByRole('switch', { name: 'Send code and logs to Gemini' })).toHaveAttribute('aria-checked', 'true')
})
