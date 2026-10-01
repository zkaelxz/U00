import { expect, test, type Page } from '@playwright/test'
import { openSettingsGroups } from './settingsNav'

// Notion export (roadmap item 112): the /api/notion routes and the export
// job are mocked (no Notion); everything else hits the real seeded API.

const ID = '01234567-89ab-cdef-0123-456789abcdef'
const PAGE_URL = 'https://www.notion.so/Signal-0123456789abcdef0123456789abcdef'
const TOKEN = 'ntn_0123456789abcdefSECRET'
const ready = { target_type: 'database', target_id: ID, token_configured: true }
const empty = { target_type: null, target_id: null, token_configured: false }

async function mockConfig(page: Page, initial: object) {
  const state = { cfg: { ...initial } as Record<string, unknown> }
  const saved: unknown[] = []
  await page.route('**/api/notion/config', async (route) => {
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON()
      saved.push(body)
      state.cfg = { ...state.cfg, ...body, ...(body.target_id !== undefined ? { target_id: body.target_id ? ID : null } : {}) }
    }
    return route.fulfill({ json: state.cfg })
  })
  return { saved, state }
}

test('settings: saves the token without showing it, saves the target, tests the connection', async ({ page }) => {
  const { saved, state } = await mockConfig(page, empty)
  const tokenBodies: unknown[] = []
  await page.route('**/api/notion/token', async (route) => {
    tokenBodies.push(route.request().postDataJSON())
    state.cfg = { ...state.cfg, token_configured: true }
    return route.fulfill({ json: state.cfg })
  })
  const clearBodies: unknown[] = []
  await page.route('**/api/notion/token/clear', async (route) => {
    clearBodies.push(route.request().postDataJSON())
    state.cfg = { ...state.cfg, token_configured: false }
    return route.fulfill({ json: state.cfg })
  })
  await page.route('**/api/notion/test', (route) =>
    route.fulfill({ json: { ok: true, bot_name: 'Baihe', target_title: 'Dramas', target_type: 'database' } }))

  await page.goto('/#/settings')
  await openSettingsGroups(page)
  const card = page.getByRole('region', { name: 'Notion' })
  await expect(card).toContainText('Not set up')
  await expect(card.getByTestId('notion-token')).toHaveText('No token')
  await expect(card.getByRole('button', { name: 'Save', exact: true })).toBeDisabled()
  await expect(card.getByRole('button', { name: 'Test connection' })).toBeDisabled()
  await expect(card.getByTestId('notion-db-note')).toContainText('"Original title" (text)')

  const tokenBox = card.getByLabel('Token', { exact: true })
  await expect(tokenBox).toHaveAttribute('type', 'password')
  await tokenBox.fill(TOKEN)
  await card.getByRole('button', { name: 'Save token' }).click()
  await expect(card.getByTestId('notion-token')).toHaveText('Token saved')
  await expect(card.getByLabel('Replace token', { exact: true })).toHaveValue('')
  expect(tokenBodies).toEqual([{ value: TOKEN, confirm: true }])
  await expect(page.locator('body')).not.toContainText(TOKEN)

  await card.getByRole('textbox', { name: 'Database link' }).fill(`https://www.notion.so/${ID.replaceAll('-', '')}`)
  await card.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(card.getByRole('status')).toHaveText('Saved.')
  await expect(card.getByRole('textbox', { name: 'Database link' })).toHaveValue(ID)
  expect(saved[0]).toEqual({ target_type: 'database', target_id: `https://www.notion.so/${ID.replaceAll('-', '')}` })
  await expect(card).toContainText('Ready, into a database')

  // Only the changed field is sent.
  await card.getByRole('combobox', { name: 'Export into' }).selectOption('page')
  await expect(card.getByTestId('notion-db-note')).toHaveCount(0)
  await card.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(card.getByRole('status')).toHaveText('Saved.')
  expect(saved[1]).toEqual({ target_type: 'page' })

  await card.getByRole('button', { name: 'Test connection' }).click()
  await expect(card.getByRole('status')).toHaveText('Connected as Baihe. Exports go into the database "Dramas".')

  // Clearing the token takes two presses.
  await card.getByRole('button', { name: 'Clear token' }).click()
  expect(clearBodies).toEqual([])
  await card.getByRole('button', { name: 'Confirm clear Notion token' }).click()
  await expect(card.getByTestId('notion-token')).toHaveText('No token')
  expect(clearBodies).toEqual([{ confirm: true }])
})

test('settings: a refused token write shows the key-writes hint', async ({ page }) => {
  await mockConfig(page, empty)
  await page.route('**/api/notion/token', (route) =>
    route.fulfill({ status: 403, json: { error: { code: 'forbidden', message: 'API key writes are disabled.' } } }))
  await page.goto('/#/settings')
  await openSettingsGroups(page)
  const card = page.getByRole('region', { name: 'Notion' })
  await card.getByLabel('Token', { exact: true }).fill(TOKEN)
  await card.getByRole('button', { name: 'Save token' }).click()
  await expect(card.getByRole('alert')).toContainText('BAIHE_API_ALLOW_KEY_WRITES=1')
  // Still at the PC: a key-write 403 does not switch the page to remote.
  await expect(card.getByRole('button', { name: 'Save token' })).toBeVisible()
})

test('export: hidden when Notion is not set up; a hint when half set up', async ({ page }) => {
  const { state } = await mockConfig(page, empty)
  await page.goto('/#/drama/1/export')
  await expect(page.getByText('More export').first()).toBeVisible()
  await expect(page.getByRole('region', { name: 'Export to Notion' })).toHaveCount(0)

  state.cfg = { ...empty, token_configured: true }
  await page.reload()
  const panel = page.getByRole('region', { name: 'Export to Notion' })
  await panel.locator('.section-title', { hasText: 'Export to Notion' }).click()
  await expect(panel).toContainText('Finish the Notion setup in Settings')
  await expect(panel.getByRole('button', { name: 'Export to Notion' })).toHaveCount(0)
})

test('export: starts a job, shows progress, then links to the page', async ({ page }) => {
  await mockConfig(page, ready)
  const pageInfo = { drama_id: 1, page_id: null as string | null, page_url: null as string | null }
  await page.route('**/api/notion/dramas/1', (route) => route.fulfill({ json: pageInfo }))
  const exports: unknown[] = []
  await page.route('**/api/notion/dramas/1/export', (route) => {
    exports.push(route.request().postDataJSON())
    if (exports.length === 1) {
      return route.fulfill({ status: 409, json: { error: { code: 'conflict', message: 'Another job is running for this drama.' } } })
    }
    return route.fulfill({ json: { job_id: 'notion_export_1' } })
  })
  let polls = 0
  const job = (over: object) => ({
    job_id: 'notion_export_1', status: 'running', progress: 0.5, message: 'Writing blocks', error: null,
    description: 'Notion export', gpu_touching: false, started_at: 1, finished_at: null, updated_at: 1, ...over,
  })
  await page.route('**/api/jobs/notion_export_1', (route) => {
    polls += 1
    if (polls < 3) return route.fulfill({ json: job({}) })
    pageInfo.page_id = 'abc'
    pageInfo.page_url = PAGE_URL
    return route.fulfill({ json: job({ status: 'done', progress: 1, message: 'Done', finished_at: 2, outcome: 'ok' }) })
  })

  await page.goto('/#/drama/1/export')
  const panel = page.getByRole('region', { name: 'Export to Notion' })
  await panel.locator('.section-title', { hasText: 'Export to Notion' }).click()
  await expect(panel).toContainText('your notes outside the Baihe transcript section are kept')
  await expect(panel.getByTestId('notion-page-link')).toHaveCount(0)

  await panel.getByRole('button', { name: 'Export to Notion' }).click()
  await expect(panel.getByRole('alert')).toHaveText('Another job is running for this drama.')

  await panel.getByRole('button', { name: 'Export to Notion' }).click()
  await expect(panel.getByTestId('job-status')).toContainText('running · Writing blocks')
  await expect(panel.getByTestId('job-percent')).toHaveText('50%')
  const link = panel.getByRole('link', { name: 'Open in Notion' })
  await expect(link).toHaveAttribute('href', PAGE_URL)
  await expect(link).toHaveAttribute('target', '_blank')
  await expect(link).toHaveAttribute('rel', 'noopener noreferrer')
  await expect(panel.getByRole('button', { name: 'Update Notion page' })).toBeEnabled()
  expect(exports).toEqual([{ field: 'en' }, { field: 'en' }])
})

test('export: a page URL outside notion.so is never linked', async ({ page }) => {
  await mockConfig(page, ready)
  await page.route('**/api/notion/dramas/1', (route) =>
    route.fulfill({ json: { drama_id: 1, page_id: 'abc', page_url: 'https://evil.example/https://www.notion.so/' } }))
  await page.goto('/#/drama/1/export')
  const panel = page.getByRole('region', { name: 'Export to Notion' })
  await panel.locator('.section-title', { hasText: 'Export to Notion' }).click()
  await expect(panel.getByRole('button', { name: 'Update Notion page' })).toBeVisible()
  await expect(panel.getByRole('link', { name: 'Open in Notion' })).toHaveCount(0)
})

test('away from the PC: no Notion export, and Settings says PC only', async ({ page }) => {
  await page.route('**/api/meta', (route) =>
    route.fulfill({ json: { app: 'baihe', api_version: '1', environment: 'development', local: false } }))
  const calls: string[] = []
  await page.route('**/api/notion/**', (route) => {
    calls.push(route.request().url())
    return route.fulfill({ json: ready })
  })
  await page.goto('/#/drama/1/export')
  await expect(page.getByText('More export').first()).toBeVisible()
  await expect(page.getByRole('region', { name: 'Export to Notion' })).toHaveCount(0)
  await page.goto('/#/settings')
  await openSettingsGroups(page)
  const card = page.getByRole('region', { name: 'Notion' })
  await expect(card).toContainText('Run this on the main PC.')
  await expect(card.getByLabel('Token', { exact: true })).toHaveCount(0)
  expect(calls).toEqual([])
})
