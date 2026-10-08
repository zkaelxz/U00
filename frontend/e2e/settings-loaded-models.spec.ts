import { expect, test, type Page } from '@playwright/test'
import { openSettingsGroups } from './settingsNav'

// Settings > Loaded now, with /api/settings/loaded-models mocked (no real
// Ollama or GPU here). Any other write is aborted and recorded.

const LOADED = {
  checked_at: '2026-10-07T10:00:00+00:00',
  ollama: { state: 'running', models: [{ name: 'gemma4:12b', size_bytes: 8_000_000_000, vram_bytes: 5_000_000_000 }] },
  app: { state: 'ok', models: [{ name: 'large-v3-turbo', kind: 'Whisper', device: 'GPU' }] },
  gpu: { state: 'ok', name: 'RTX 4090', total_bytes: 24_000_000_000, used_bytes: 9_000_000_000, free_bytes: 15_000_000_000 },
  memory: { vram: { state: 'unknown', total_bytes: null, free_bytes: null, reserved_bytes: 0 }, ram: { state: 'unknown', total_bytes: null, free_bytes: null, reserved_bytes: 0 } },
  llama_cpp_running: false,
  gpu_job_running: false,
}
const EMPTY = {
  ...LOADED,
  ollama: { state: 'not_running', models: [] },
  app: { state: 'ok', models: [] },
  gpu: { state: 'unknown' },
}

async function mock(page: Page, first: object) {
  const posts: string[] = []
  const unmocked: string[] = []
  let current: object = first
  await page.route('**/api/**', (route) => {
    const r = route.request()
    if (r.method() === 'GET') return route.fallback()
    unmocked.push(`${r.method()} ${r.url()}`)
    return route.abort()
  })
  await page.route('**/api/settings/loaded-models**', (route) => {
    const r = route.request()
    if (r.method() === 'POST') {
      posts.push(new URL(r.url()).pathname)
      current = EMPTY
    }
    return route.fulfill({ json: current })
  })
  await page.route('**/api/meta', (route) =>
    route.fulfill({ json: { app: 'baihe', api_version: '1', environment: 'development', local: true } }),
  )
  return { posts, unmocked, set: (v: object) => { current = v } }
}

test('lists what is loaded and frees app models after a confirm', async ({ page }) => {
  const { posts, unmocked } = await mock(page, LOADED)
  await page.goto('/#/settings')
  await openSettingsGroups(page)
  const card = page.getByRole('region', { name: 'Loaded now', exact: true })
  const table = card.getByTestId('loaded-table')
  await expect(table).toContainText('gemma4:12b')
  await expect(table).toContainText('Ollama · 63% GPU / 38% CPU')
  await expect(table).toContainText('large-v3-turbo')
  await expect(card.getByTestId('loaded-gpu')).toHaveText('RTX 4090: 9.0 GB used of 24.0 GB (15.0 GB free)')
  await expect(card.getByTestId('loaded-llama')).toHaveText('llama.cpp server: not found')
  await expect(card).toContainText('Last refreshed')

  await card.getByRole('button', { name: /^Free app models/ }).click()
  expect(posts).toEqual([])
  await card.getByRole('button', { name: /^Confirm free/ }).click()
  await expect(card.getByTestId('loaded-empty')).toContainText("Ollama isn't running.")
  expect(posts).toEqual(['/api/settings/loaded-models/free-app-models'])
  expect(unmocked).toEqual([])
})

test('empty states are honest and Refresh re-reads', async ({ page }) => {
  const { set } = await mock(page, EMPTY)
  await page.goto('/#/settings')
  await openSettingsGroups(page)
  const card = page.getByRole('region', { name: 'Loaded now', exact: true })
  await expect(card.getByTestId('loaded-empty')).toContainText('Nothing loaded.')
  await expect(card.getByTestId('loaded-gpu')).toHaveText('GPU info unavailable.')
  set(LOADED)
  await card.getByRole('button', { name: 'Refresh' }).click()
  await expect(card.getByTestId('loaded-table')).toContainText('gemma4:12b')
})

test('free is disabled while a GPU job runs', async ({ page }) => {
  await mock(page, { ...LOADED, gpu_job_running: true })
  await page.goto('/#/settings')
  await openSettingsGroups(page)
  const card = page.getByRole('region', { name: 'Loaded now', exact: true })
  await expect(card.getByRole('button', { name: /^Free app models/ })).toBeDisabled()
  await expect(card).toContainText('GPU job is running')
})

test('away from the PC there is no free button', async ({ page }) => {
  await mock(page, LOADED)
  await page.route('**/api/meta', (route) =>
    route.fulfill({ json: { app: 'baihe', api_version: '1', environment: 'development', local: false } }),
  )
  await page.goto('/#/settings')
  await openSettingsGroups(page)
  const card = page.getByRole('region', { name: 'Loaded now', exact: true })
  await expect(card.getByTestId('loaded-table')).toBeVisible()
  await expect(card.getByRole('button', { name: /Free app models/ })).toHaveCount(0)
})
