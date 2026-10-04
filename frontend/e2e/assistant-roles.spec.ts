import { expect, test } from '@playwright/test'

import { mockAssistant } from './assistantMocks'

// The independent review role. Every /api/assistant call is mocked.

const CONCERNS = {
  engine: 'gemini',
  model: null,
  verdict: 'concerns',
  notes: 'default_voice is not defined in dub_service.py, so this patch raises NameError.',
  tool_calls: [{ id: 'r1', name: 'search_code', args: { query: 'default_voice' }, ok: true, summary: 'No matches.' }],
}

test('a fix the reviewer flags is shown with both views', async ({ page }) => {
  const s = await mockAssistant(page, { developerMode: true, rolesEnabled: true, reviewEngine: 'gemini', review: CONCERNS })
  await page.goto('/#/assistant')
  const chat = page.getByRole('region', { name: 'Ask the assistant' })
  await chat.getByRole('textbox', { name: 'Question' }).fill('Why does dub skip lines?')
  await chat.getByRole('button', { name: 'Ask', exact: true }).click()
  // The implementing engine's fix is still shown...
  await expect(chat.getByRole('figure', { name: 'Proposed fix' })).toContainText('default_voice')
  // ...with the reviewer's concern next to it.
  const review = chat.getByTestId('assistant-review')
  await expect(review).toContainText('Concerns')
  await expect(review).toContainText('NameError')
  await expect(review).toContainText('Gemini')
  expect(s.unmocked).toEqual([])
})

test('turning on the review and picking a different engine saves both', async ({ page }) => {
  const s = await mockAssistant(page, { developerMode: true, engine: 'claude' })
  await page.goto('/#/assistant')
  const chat = page.getByRole('region', { name: 'Ask the assistant' })
  await chat.locator('summary').filter({ hasText: 'Independent review' }).click()
  await chat.getByRole('switch', { name: 'Review each proposed fix' }).click()
  await expect(chat.getByText('Pick a review engine.')).toBeVisible()
  await chat.getByLabel('Review engine', { exact: true }).selectOption('claude')
  await expect(chat.getByText('Pick an engine different from the one that answers.')).toBeVisible()
  await chat.getByLabel('Review engine', { exact: true }).selectOption('ollama')
  await chat.getByRole('button', { name: 'Save review engine' }).click()
  await expect(chat.getByText('Pick an engine')).toHaveCount(0)
  const posts = s.calls.filter((c) => c.method === 'POST' && c.path === '/api/assistant/settings').map((c) => c.body)
  expect(posts).toEqual([{ roles_enabled: true }, { review_engine: 'ollama', review_model: null }])
})
