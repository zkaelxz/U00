import { expect, test } from '@playwright/test'

import { mockAssistant } from './assistantMocks'

// Tiered escalation: the user asks the next tier after an answer, confirms what
// is sent, and every answer says which tier and engine gave it.

const asks = (s: Awaited<ReturnType<typeof mockAssistant>>) =>
  s.calls.filter((c) => c.method === 'POST' && c.path === '/api/assistant/ask').map((c) => c.body as Record<string, unknown>)

test('ask on Ollama, escalate to Gemini with consent, answer labelled with its tier', async ({ page }) => {
  const s = await mockAssistant(page, {
    developerMode: true, tiers: ['ollama', 'gemini'], cloudConsent: { claude: false, gemini: false }, rolesEnabled: true, reviewEngine: 'claude',
  })
  await page.goto('/#/assistant')
  const chat = page.getByRole('region', { name: 'Ask the assistant' })
  await chat.getByRole('textbox', { name: 'Question' }).fill('Why does the Dub stage skip lines?')
  await chat.getByRole('button', { name: 'Ask', exact: true }).click()
  await expect(chat.getByTestId('assistant-tier').first()).toHaveText('Tier 1 · Ollama · on this PC')

  await chat.getByRole('button', { name: 'Ask a stronger model (Gemini)' }).click()
  const dialog = page.getByRole('dialog', { name: 'Ask Gemini?' })
  await expect(dialog).toContainText('This leaves your PC and goes to Gemini')
  await expect(dialog).toContainText('Google may use what you send to improve its products')
  await expect(dialog).toContainText('Your question.')
  await expect(dialog).toContainText('What the earlier tiers’ read-only tools found')
  // Nothing is sent before the user confirms, and not before the provider is allowed.
  const send = dialog.getByRole('button', { name: 'Send to Gemini' })
  await expect(send).toBeDisabled()
  expect(asks(s)).toHaveLength(1)
  await dialog.getByRole('switch', { name: 'Send code and logs to Gemini' }).click()
  await expect(send).toBeEnabled()
  await send.click()

  await expect(chat.getByTestId('assistant-tier')).toHaveCount(2)
  await expect(chat.getByTestId('assistant-tier').nth(1)).toHaveText('Tier 2 · Gemini · cloud')
  await expect(chat.getByTestId('assistant-review-skipped')).toHaveText(/The reviewer is a cloud engine/)
  const escalated = asks(s)[1]
  expect(escalated).toMatchObject({ question: 'Why does the Dub stage skip lines?', engine: 'gemini', escalate: true, consent: true, chat_history: [] })
  expect(String(escalated.evidence)).toContain('RESULT t1 (inspect_logs, ok)')
  // Gemini is the last tier: no further escalation, only the developer report.
  const lastActions = chat.getByTestId('assistant-turn-actions').nth(1)
  await expect(lastActions.getByRole('button', { name: /Ask a stronger model/ })).toHaveCount(0)
  await lastActions.getByRole('button', { name: 'Prepare a report for a developer' }).click()
  const report = page.getByRole('dialog', { name: 'Report for a developer' })
  await expect(report).toContainText('not sent anywhere')
  await expect(report).toContainText('You: Why does the Dub stage skip lines?')
  await expect(report.getByRole('button', { name: 'Copy report' })).toBeVisible()
  expect(s.unmocked).toEqual([])
})

test('a tier that fails says so plainly and offers the next tier without sending to it', async ({ page }) => {
  const s = await mockAssistant(page, { developerMode: true, tiers: ['ollama', 'gemini'], cloudConsent: { claude: false, gemini: true }, failing: ['ollama'] })
  await page.goto('/#/assistant')
  const chat = page.getByRole('region', { name: 'Ask the assistant' })
  await chat.getByRole('textbox', { name: 'Question' }).fill('Why?')
  await chat.getByRole('button', { name: 'Ask', exact: true }).click()
  await expect(chat.getByRole('alert').first()).toContainText('Check that Ollama is running on this PC')
  await expect(chat.getByRole('button', { name: 'Ask a stronger model (Gemini)' })).toBeVisible()
  expect(asks(s).map((b) => b.engine ?? 'default')).toEqual(['default'])
  await chat.getByRole('button', { name: 'Ask a stronger model (Gemini)' }).click()
  const dialog = page.getByRole('dialog', { name: 'Ask Gemini?' })
  await expect(dialog).toContainText('No tool output')
  await dialog.getByRole('button', { name: 'Cancel' }).click()
  expect(asks(s)).toHaveLength(1)
})
