import { expect, test, type Page } from '@playwright/test'
import { openSettingsGroups } from './settingsNav'

// Jellyfin connector: the /api/jellyfin routes are mocked (no
// Jellyfin server); everything else hits the real seeded API.

const on = { enabled: true, server_url: 'http://192.168.1.20:8096', library_dir: 'D:\\Media', key_configured: true }
const report = {
  language: 'en', total: 3, with_subtitles: 1, missing: 2, truncated: false,
  items: [
    { id: 'ep1', name: 'Pilot', series: 'Show', season: 1, episode: 1, type: 'episode', writable: true },
    { id: 'mv2', name: 'Elsewhere', series: null, season: null, episode: null, type: 'movie', writable: false },
  ],
}

async function mockConfig(page: Page, initial: typeof on) {
  const state = { cfg: { ...initial } }
  const saved: unknown[] = []
  await page.route('**/api/jellyfin/config', async (route) => {
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON()
      saved.push(body)
      const { confirm: _c, ...rest } = body
      void _c
      state.cfg = { ...state.cfg, ...rest }
    }
    return route.fulfill({ json: state.cfg })
  })
  return { saved, state }
}

test('settings: off by default, saves the address, never shows the key, scan is a read-only report', async ({ page }) => {
  const { saved, state } = await mockConfig(page, { enabled: false, server_url: null, library_dir: null, key_configured: false } as never)
  const keyBodies: unknown[] = []
  await page.route('**/api/jellyfin/key', async (route) => {
    keyBodies.push(route.request().postDataJSON())
    state.cfg = { ...state.cfg, key_configured: true }
    return route.fulfill({ json: state.cfg })
  })
  await page.route('**/api/jellyfin/test', (route) => route.fulfill({ json: { ok: true, server_name: 'Den', version: '10.9.0' } }))
  await page.route('**/api/jellyfin/scan', (route) => route.fulfill({ json: report }))
  await page.goto('/#/settings')
  await openSettingsGroups(page)
  const card = page.getByRole('region', { name: 'Jellyfin' })
  await expect(card).toContainText('Off')
  const sw = card.getByRole('switch', { name: 'Use Jellyfin' })
  await expect(sw).toHaveAttribute('aria-checked', 'false')
  await expect(card.getByRole('button', { name: 'Scan library' })).toHaveCount(0)

  await card.getByRole('textbox', { name: 'Server address' }).fill('http://192.168.1.20:8096')
  await card.getByRole('textbox', { name: 'Library folder' }).fill('D:\\Media')
  await card.getByRole('button', { name: 'Save', exact: true }).click()
  expect(saved[0]).toEqual({ server_url: 'http://192.168.1.20:8096', library_dir: 'D:\\Media', confirm: true })

  const keyBox = card.getByLabel('API key', { exact: true })
  await keyBox.fill('0123456789abcdef0123456789abcdef')
  await card.getByRole('button', { name: 'Save key' }).click()
  await expect(card.getByTestId('jellyfin-key')).toHaveText('Set')
  await expect(card.getByLabel('Replace API key', { exact: true })).toHaveValue('')
  expect(keyBodies).toEqual([{ value: '0123456789abcdef0123456789abcdef', confirm: true }])

  await card.getByRole('button', { name: 'Test connection' }).click()
  await expect(card.getByRole('status')).toHaveText('Connected to Den 10.9.0.')

  await sw.click()
  await expect(sw).toHaveAttribute('aria-checked', 'true')
  await card.getByRole('button', { name: 'Scan library' }).click()
  const r = card.getByTestId('jellyfin-report')
  await expect(r).toContainText('3 items: 1 have EN subtitles, 2 are missing them. Nothing was changed.')
  await expect(r.getByRole('list', { name: 'Missing subtitles' })).toContainText('Show · S01E01 · Pilot')
  await expect(r).toContainText('outside the library folder')
})

test('export: Send to Jellyfin is hidden while the connector is off', async ({ page }) => {
  await mockConfig(page, { ...on, enabled: false })
  await page.goto('/#/drama/1/export')
  await expect(page.getByRole('heading', { name: 'Export' }).or(page.getByText('Video and audio')).first()).toBeVisible()
  await expect(page.getByRole('region', { name: 'Send to Jellyfin' })).toHaveCount(0)
})

test('export: sends next to a Jellyfin video and never overwrites unless asked', async ({ page }) => {
  await mockConfig(page, on)
  await page.route('**/api/jellyfin/scan', (route) => route.fulfill({ json: report }))
  const sends: unknown[] = []
  await page.route('**/api/jellyfin/dramas/1/send', (route) => {
    const body = route.request().postDataJSON()
    sends.push(body)
    if (!body.overwrite) {
      return route.fulfill({ status: 409, json: { error: { code: 'conflict', message: 'S01E01.eng.srt already exists in the library. Allow replacing it to overwrite.' } } })
    }
    return route.fulfill({ json: { drama_id: 1, files: ['Show/S01E01.eng.srt'], refresh: 'done' } })
  })
  await page.goto('/#/drama/1/export')
  const panel = page.getByRole('region', { name: 'Send to Jellyfin' })
  await panel.locator('.section-title', { hasText: 'Send to Jellyfin' }).click()
  await panel.getByRole('combobox', { name: 'Where' }).selectOption('item')
  await panel.getByRole('button', { name: 'Find videos missing subtitles' }).click()
  const video = panel.getByRole('combobox', { name: 'Jellyfin video' })
  await expect(video.locator('option')).toHaveCount(1) // only the one inside the library folder
  await panel.getByRole('button', { name: 'Send to Jellyfin' }).click()
  await expect(panel.getByRole('alert')).toContainText('already exists')
  await panel.getByRole('switch', { name: 'Replace existing files' }).click()
  await panel.getByRole('button', { name: 'Send to Jellyfin' }).click()
  await expect(panel.getByRole('status')).toHaveText('Saved Show/S01E01.eng.srt. Jellyfin is rescanning the library.')
  expect(sends).toEqual([
    { format: 'srt', field: 'en', language: 'en', overwrite: false, refresh: true, media: 'none', item_id: 'ep1' },
    { format: 'srt', field: 'en', language: 'en', overwrite: true, refresh: true, media: 'none', item_id: 'ep1' },
  ])
})
