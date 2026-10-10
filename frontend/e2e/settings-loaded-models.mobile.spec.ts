import { expect, test } from '@playwright/test'
import { openSettingsGroups } from './settingsNav'

test('Loaded now fits a phone: no sideways page scroll, 44px buttons', async ({ page }) => {
  await page.route('**/api/settings/loaded-models**', (route) =>
    route.fulfill({
      json: {
        checked_at: '2026-10-07T10:00:00+00:00',
        ollama: { state: 'running', models: [{ name: 'gemma4:12b-instruct-q4_K_M', size_bytes: 8_000_000_000, vram_bytes: 8_000_000_000 }] },
        app: { state: 'ok', models: [{ name: 'large-v3-turbo', kind: 'Whisper', device: 'GPU' }] },
        gpu: { state: 'ok', name: 'NVIDIA GeForce RTX 4090', total_bytes: 24_000_000_000, used_bytes: 9_000_000_000, free_bytes: 15_000_000_000 },
        memory: { vram: { state: 'ok', total_bytes: 24_000_000_000, free_bytes: 15_000_000_000, reserved_bytes: 4_000_000_000 }, ram: { state: 'unknown', total_bytes: null, free_bytes: null, reserved_bytes: 2_000_000_000 } },
        llama_cpp_running: false,
        gpu_job_running: false,
      },
    }),
  )
  await page.route('**/api/meta', (route) =>
    route.fulfill({ json: { app: 'baihe', api_version: '1', environment: 'development', local: true } }),
  )
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'System')
  const card = page.getByRole('region', { name: 'Loaded now', exact: true })
  await expect(card.getByTestId('loaded-table')).toBeVisible()
  for (const b of await card.getByRole('button').all()) {
    expect((await b.boundingBox())!.height).toBeGreaterThanOrEqual(43)
  }
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)
  expect(overflow).toBe(false)
})
