import { expect, test } from '@playwright/test'

import { ANSWER, mockAssistant } from './assistantMocks'
import { navLink, openMenu, openSettingsGroups } from './settingsNav'

// Desktop: the Maintenance assistant. Every /api/assistant call is mocked.

const SHOTS = '/tmp/claude-0/-home-user-U00/780be93c-8b60-5332-b9fb-fd0d9036666f/scratchpad/shots'

test('nav link is hidden with Developer Mode off and appears once it is turned on in Settings', async ({ page }) => {
  const s = await mockAssistant(page)
  await page.goto('/#/settings')
  await openSettingsGroups(page)
  await openMenu(page)
  await expect(navLink(page, 'Settings')).toBeVisible()
  const toggle = page.getByRole('region', { name: 'Developer Mode' }).getByRole('switch', { name: 'Developer Mode' })
  await expect(toggle).not.toBeChecked()
  await expect(navLink(page, 'Assistant')).toHaveCount(0)
  await toggle.click()
  await expect(toggle).toBeChecked()
  await openMenu(page) // clicking the switch closed the menu
  await expect(navLink(page, 'Assistant')).toBeVisible()
  expect(s.calls.find((c) => c.method === 'POST')?.body).toEqual({ developer_mode: true })
  await navLink(page, 'Assistant').click()
  await expect(page.getByRole('heading', { name: 'Maintenance assistant' })).toBeVisible()
  expect(s.unmocked).toEqual([])
})

test('reached by URL with the mode off: only the Developer Mode switch', async ({ page }) => {
  const s = await mockAssistant(page)
  await page.goto('/#/assistant')
  const off = page.getByRole('region', { name: 'Developer Mode is off' })
  await expect(off).toBeVisible()
  await expect(page.getByRole('region', { name: 'Ask the assistant' })).toHaveCount(0)
  await off.getByRole('switch', { name: 'Developer Mode' }).click()
  await expect(page.getByRole('region', { name: 'Ask the assistant' })).toBeVisible()
  await openMenu(page)
  await expect(navLink(page, 'Assistant')).toBeVisible()
  expect(s.unmocked).toEqual([])
})

test('from another device: PC only, no nav link, no Settings card', async ({ page }) => {
  const s = await mockAssistant(page, { local: false, developerMode: true })
  await page.goto('/#/assistant')
  await expect(page.getByText('The maintenance assistant is available on the PC only.')).toBeVisible()
  await expect(navLink(page, 'Assistant')).toHaveCount(0)
  await page.goto('/#/settings')
  await openSettingsGroups(page)
  await expect(page.getByRole('region', { name: 'Jobs' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Developer Mode' })).toHaveCount(0)
  expect(s.unmocked).toEqual([])
})

test('ask: busy state, plain-text answer, tools used, patch not applied, add suggestion to backlog', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  let release!: () => void
  const s = await mockAssistant(page, { developerMode: true, askGate: new Promise<void>((r) => (release = r)) })
  await page.goto('/#/assistant')
  await openMenu(page)
  await expect(navLink(page, 'Assistant')).toBeVisible()
  await expect(page.getByTestId('read-only-note')).toHaveText('Read-only: this assistant has no tool that changes files, git or settings.')

  const chat = page.getByRole('region', { name: 'Ask the assistant' })
  await chat.getByRole('textbox', { name: 'Question' }).fill('Why does dub skip lines?')
  await chat.getByRole('button', { name: 'Ask', exact: true }).click()
  await expect(chat.getByRole('button', { name: 'Asking…' })).toBeDisabled()
  await expect(chat.getByText('Working on it. This can take a minute.')).toBeVisible()
  release()

  const answer = chat.getByTestId('assistant-answer')
  await expect(answer).toContainText('The Dub stage skips a line when it has no speaker.')
  // Plain text: markup stays literal.
  await expect(answer).toContainText('<b>not bold</b>')
  await expect(answer.locator('b')).toHaveCount(0)

  await chat.getByText('Tools used (2)').click()
  const tools = chat.getByRole('list', { name: 'Tools used' })
  await expect(tools.getByRole('listitem')).toHaveCount(2)
  await expect(tools).toContainText('search_code')
  await expect(tools).toContainText('{"query":"speaker","path":"services"}')
  await expect(tools.getByText('Failed')).toBeVisible()

  const patch = chat.getByRole('figure', { name: 'Proposed fix' })
  await expect(patch).toContainText('Proposed fix — not applied. Review it and apply it by hand.')
  await expect(patch.locator('pre')).toContainText('+    if not line.speaker and not default_voice:')
  await patch.getByRole('button', { name: 'Copy proposed fix' }).click()
  await expect(patch.getByText('Copied.')).toBeVisible()
  expect(await page.evaluate(() => navigator.clipboard.readText())).toContain('default_voice')

  const suggestion = chat.getByRole('list', { name: 'Suggested backlog items' }).getByRole('listitem')
  await suggestion.getByRole('button', { name: 'Add to backlog' }).click()
  await expect(suggestion.getByRole('button', { name: 'Added' })).toBeDisabled()
  const backlog = page.getByRole('list', { name: 'Backlog items', exact: true })
  await expect(backlog.getByRole('listitem')).toHaveCount(2)
  await expect(backlog).toContainText('Dub skips lines with no speaker')

  // A follow-up sends the first exchange as history.
  await chat.getByRole('textbox', { name: 'Question' }).fill('And the fix?')
  await chat.getByRole('button', { name: 'Ask', exact: true }).click()
  await expect(chat.getByTestId('assistant-answer')).toHaveCount(2)
  const asks = s.calls.filter((c) => c.path === '/api/assistant/ask')
  expect(asks[0].body).toEqual({ question: 'Why does dub skip lines?', chat_history: [] })
  expect(asks[1].body).toEqual({
    question: 'And the fix?',
    chat_history: [
      { role: 'user', content: 'Why does dub skip lines?' },
      { role: 'assistant', content: ANSWER.answer },
    ],
  })
  expect(s.unmocked).toEqual([])
})

test('ask errors are plain text; a 409 falls back to the Developer Mode switch', async ({ page }) => {
  const s = await mockAssistant(page, { developerMode: true, askStatus: 503 })
  await page.goto('/#/assistant')
  const chat = page.getByRole('region', { name: 'Ask the assistant' })
  await chat.getByRole('textbox', { name: 'Question' }).fill('Hi')
  await chat.getByRole('button', { name: 'Ask', exact: true }).click()
  await expect(chat.getByRole('alert')).toHaveText('No API key is set for that engine. Add one in Settings, or pick another engine.')
  s.developerMode = false
  await chat.getByRole('textbox', { name: 'Question' }).fill('Again')
  await chat.getByRole('button', { name: 'Ask', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Developer Mode is off' })).toBeVisible()
  expect(s.unmocked).toEqual([])
})

test('engine picker sends the pick and saves only what changed', async ({ page }) => {
  const s = await mockAssistant(page, { developerMode: true, engine: 'claude' })
  await page.goto('/#/assistant')
  const chat = page.getByRole('region', { name: 'Ask the assistant' })
  await expect(chat.getByLabel('Engine', { exact: true })).toHaveValue('claude')
  const save = chat.getByRole('button', { name: 'Save as default' })
  await expect(save).toBeDisabled()
  await chat.getByLabel('Engine', { exact: true }).selectOption('gemini')
  await save.click()
  await expect(chat.getByText('Saved as the default.')).toBeVisible()
  expect(s.calls.filter((c) => c.method === 'POST' && c.path === '/api/assistant/settings').map((c) => c.body)).toEqual([{ engine: 'gemini' }])
  await chat.getByRole('textbox', { name: 'Question' }).fill('Hi')
  await chat.getByRole('button', { name: 'Ask', exact: true }).click()
  await expect(chat.getByTestId('assistant-answer')).toBeVisible()
  expect(s.calls.find((c) => c.path === '/api/assistant/ask')?.body).toEqual({ question: 'Hi', chat_history: [], engine: 'gemini' })
})

test('backlog: add, delete (two-step), clear all; changelog', async ({ page }) => {
  const s = await mockAssistant(page, { developerMode: true })
  await page.goto('/#/assistant')
  const card = page.getByRole('region', { name: 'Backlog' })
  const list = card.getByRole('list', { name: 'Backlog items', exact: true })
  await expect(list.getByRole('listitem')).toHaveCount(1)
  await expect(card.locator('.card-meta')).toHaveText('1 item')

  await card.getByLabel('Kind').selectOption('feature')
  await card.getByLabel('New item').fill('A dark theme for the reader.')
  await card.getByRole('button', { name: 'Add', exact: true }).click()
  await expect(list.getByRole('listitem')).toHaveCount(2)
  await expect(list.getByRole('listitem').nth(1)).toContainText('Feature')
  await expect(card.getByLabel('New item')).toHaveValue('')

  await card.getByRole('button', { name: 'Delete backlog item: Tidy the Export stage copy.' }).click()
  await card.getByRole('button', { name: 'Confirm delete' }).click()
  await expect(list.getByRole('listitem')).toHaveCount(1)
  expect(s.calls.find((c) => c.path === '/api/assistant/backlog/1/delete')?.body).toEqual({ confirm: true })

  await card.getByRole('button', { name: 'Clear all backlog items' }).click()
  await card.getByRole('button', { name: 'Confirm clear all backlog items' }).click()
  await expect(card.getByText('Nothing in the backlog yet.')).toBeVisible()
  expect(s.calls.find((c) => c.path === '/api/assistant/backlog/clear')?.body).toEqual({ confirm: true })

  const log = page.getByRole('region', { name: 'Changelog' })
  await expect(log.getByLabel('To', { exact: true })).toHaveValue('HEAD')
  await log.getByLabel('From', { exact: true }).fill('v0.9')
  await log.getByRole('button', { name: 'Generate' }).click()
  await expect(log.getByTestId('changelog-result')).toContainText('2 commits from v0.9 to HEAD')
  await expect(log.locator('pre')).toContainText('Added the maintenance assistant.')
  expect(s.calls.find((c) => c.path === '/api/assistant/changelog')?.body).toEqual({ from_ref: 'v0.9', to_ref: 'HEAD' })
  expect(s.unmocked).toEqual([])
})

test('desktop screenshot', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await mockAssistant(page, { developerMode: true })
  await page.goto('/#/assistant')
  const chat = page.getByRole('region', { name: 'Ask the assistant' })
  await chat.getByRole('textbox', { name: 'Question' }).fill('Why does dub skip lines?')
  await chat.getByRole('button', { name: 'Ask', exact: true }).click()
  await expect(chat.getByTestId('assistant-answer')).toBeVisible()
  await chat.getByText('Tools used (2)').click()
  await page.getByRole('region', { name: 'Tools' }).getByText('What it can read').click()
  await page.screenshot({ path: `${SHOTS}/assistant-desktop.png`, fullPage: true })
})
