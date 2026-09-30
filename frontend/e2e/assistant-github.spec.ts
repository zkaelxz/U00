import { expect, test } from '@playwright/test'

import { ANSWER, mockAssistant } from './assistantMocks'

// Deliver a proposed fix as a GitHub pull request. Every /api/assistant call is mocked.

const READY = { enabled: true, repo: 'me/app', base_branch: 'baihe-subtitler', token_configured: true, branch_prefix: 'baihe-assistant/' }

async function ask(page: import('@playwright/test').Page) {
  await page.goto('/#/assistant')
  const chat = page.getByRole('region', { name: 'Ask the assistant' })
  await chat.getByRole('textbox', { name: 'Question' }).fill('Why does dub skip lines?')
  await chat.getByRole('button', { name: 'Ask', exact: true }).click()
  return chat
}

test('off by default: no Deliver button, and no GitHub call beyond the status read', async ({ page }) => {
  const s = await mockAssistant(page, { developerMode: true })
  const chat = await ask(page)
  const fix = chat.getByRole('figure', { name: 'Proposed fix' })
  await expect(fix).toContainText('GitHub delivery is off.')
  await expect(fix.getByRole('button', { name: /Deliver as GitHub PR/ })).toHaveCount(0)
  expect(s.calls.filter((c) => c.path.startsWith('/api/assistant/github')).map((c) => `${c.method} ${c.path}`)).toEqual(['GET /api/assistant/github'])
})

test('preview shows the exact diff, then one confirm opens a draft PR', async ({ page }) => {
  const s = await mockAssistant(page, { developerMode: true, github: READY })
  const chat = await ask(page)
  const fix = chat.getByRole('figure', { name: 'Proposed fix' })
  await fix.getByRole('button', { name: 'Deliver as GitHub PR…' }).click()
  await fix.getByLabel('Pull request title', { exact: true }).fill('Dub: keep lines with a default voice')
  await fix.getByRole('button', { name: 'Preview pull request' }).click()
  const preview = fix.getByTestId('github-preview')
  await expect(preview).toContainText('Nothing is pushed to baihe-subtitler')
  await expect(preview.locator('pre')).toHaveText(ANSWER.proposed_patches[0].patch)
  expect(s.calls.some((c) => c.path.endsWith('/deliver'))).toBe(false)
  await preview.getByRole('button', { name: /Open draft pull request/ }).click()
  expect(s.calls.some((c) => c.path.endsWith('/deliver'))).toBe(false) // first press only arms it
  await preview.getByRole('button', { name: 'Confirm: open the draft pull request' }).click()
  await expect(fix.getByRole('link', { name: '#7' })).toHaveAttribute('href', 'https://github.com/me/app/pull/7')
  const deliver = s.calls.find((c) => c.path.endsWith('/deliver'))
  expect(deliver?.body).toEqual({
    patch: ANSWER.proposed_patches[0].patch, title: 'Dub: keep lines with a default voice', body: '', sha256: 'f'.repeat(64), confirm: true,
  })
})

test('settings: turn on, set the repo, save a token (never shown back), test', async ({ page }) => {
  const s = await mockAssistant(page, { developerMode: true, github: { ...READY, enabled: false, repo: null, token_configured: false } })
  await page.goto('/#/assistant')
  const card = page.getByRole('region', { name: 'GitHub pull requests' })
  await card.getByRole('switch', { name: 'Deliver fixes as pull requests' }).click()
  await card.getByLabel('Repository', { exact: true }).fill('me/app')
  await card.getByRole('button', { name: 'Save repository' }).click()
  await card.getByLabel('Token', { exact: true }).fill('ghp_secretsecret')
  await card.getByRole('button', { name: /Save token/ }).click()
  await card.getByRole('button', { name: 'Confirm: save the GitHub token' }).click()
  await expect(card.getByText('Set', { exact: true })).toBeVisible()
  await expect(card.getByLabel('Token', { exact: true })).toHaveValue('')
  await card.getByRole('button', { name: 'Test connection' }).click()
  await expect(card.getByTestId('github-connection')).toContainText('the token can push')
  const tokenPost = s.calls.find((c) => c.path === '/api/assistant/github/token')
  expect(tokenPost?.body).toEqual({ value: 'ghp_secretsecret', confirm: true })
  await expect(page.locator('body')).not.toContainText('ghp_secretsecret')
})

test('a token write refused by the key-write gate says how to turn it on', async ({ page }) => {
  await mockAssistant(page, { developerMode: true, github: READY, tokenStatus: 403 })
  await page.goto('/#/assistant')
  const card = page.getByRole('region', { name: 'GitHub pull requests' })
  await card.getByLabel('Token', { exact: true }).fill('ghp_x')
  await card.getByRole('button', { name: /Save token/ }).click()
  await card.getByRole('button', { name: 'Confirm: save the GitHub token' }).click()
  await expect(card.getByRole('alert')).toContainText('BAIHE_API_ALLOW_KEY_WRITES=1')
})

test('changing the base branch after a preview drops that preview', async ({ page }) => {
  const s = await mockAssistant(page, { developerMode: true, github: READY })
  const chat = await ask(page)
  const fix = chat.getByRole('figure', { name: 'Proposed fix' })
  await fix.getByRole('button', { name: 'Deliver as GitHub PR…' }).click()
  await fix.getByRole('button', { name: 'Preview pull request' }).click()
  await expect(fix.getByTestId('github-preview')).toBeVisible()
  const card = page.getByRole('region', { name: 'GitHub pull requests' })
  await card.getByLabel('Base branch', { exact: true }).fill('main')
  await card.getByRole('button', { name: 'Save repository' }).click()
  await expect(fix.getByTestId('github-preview')).toHaveCount(0)
  expect(s.calls.some((c) => c.path.endsWith('/deliver'))).toBe(false)
})
