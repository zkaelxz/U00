import { expect, test, type Page, type Route } from '@playwright/test'
import { openSettingsGroups } from './settingsNav'

// Settings > App updates, desktop. /api/system/update* is mocked (the real
// check would reach GitHub); the rest of the page uses the seeded API.

const BASE = {
  current: '0.1.0', installed: true, latest: null, update_available: false, notes: '', installer_name: null,
  size: null, checked_at: null, check_error: null, release_lookup: 'unchecked', download: 'idle', downloaded_bytes: 0, download_error: null,
  verified: false, verified_version: null, verified_name: null, can_install: false, auto_check: false,
  custom_source: false,
}
const AVAILABLE = {
  ...BASE, latest: '0.2.0', update_available: true, installer_name: 'BaiheStudio-Setup-0.2.0.exe',
  size: 104_000_000, checked_at: 1759000000, release_lookup: 'found', notes: "What's new\n* Faster export",
}

const card = (page: Page) => page.getByRole('region', { name: 'App updates', exact: true })

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })

async function mockUpdates(page: Page, initial: Record<string, unknown> = {}) {
  const calls: { method: string; path: string; body: unknown }[] = []
  let status: Record<string, unknown> = { ...BASE, ...initial }
  let polls = 0
  await page.route((u) => u.pathname.startsWith('/api/system/update'), async (route) => {
    const req = route.request()
    const path = new URL(req.url()).pathname
    calls.push({ method: req.method(), path, body: req.postDataJSON() })
    if (req.method() === 'GET' && path === '/api/system/update') {
      if (status.download === 'downloading' && ++polls >= 2) {
        status = {
          ...status, download: 'verified', downloaded_bytes: 104_000_000, verified: true, can_install: true,
          verified_version: '0.2.0', verified_name: AVAILABLE.installer_name,
        }
      }
      return json(route, status)
    }
    if (path.endsWith('/check')) return json(route, (status = { ...AVAILABLE }))
    if (path.endsWith('/download')) {
      return json(route, (status = { ...status, download: 'downloading', downloaded_bytes: 52_000_000 }), 202)
    }
    if (path.endsWith('/install')) return json(route, { launched: true, installer_name: AVAILABLE.installer_name, version: '0.2.0' })
    if (path.endsWith('/settings')) return json(route, (status = { ...status, auto_check: true }))
    return route.abort()
  })
  return calls
}

test('check, download and verify, then open Setup behind a confirm', async ({ page }) => {
  const calls = await mockUpdates(page)
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'Preferences')
  const c = card(page)
  await expect(c.locator('.card-meta')).toHaveText('Version 0.1.0')
  await expect(c.getByTestId('update-check-line')).toHaveText('Not checked yet.')
  await expect(c).toContainText('not code-signed')
  await expect(c).toContainText('it is not a signature')
  await expect(c.getByRole('switch', { name: 'Check once a day' })).not.toBeChecked()
  await expect(c.getByRole('button', { name: 'Download' })).toHaveCount(0)

  await c.getByRole('button', { name: 'Check for updates' }).click()
  await expect(c.getByTestId('update-check-line')).toHaveText('Version 0.2.0 is available (104.0 MB).')
  await c.getByText("What's new in 0.2.0").click()
  await expect(c).toContainText('Faster export')

  await c.getByRole('button', { name: 'Download' }).click()
  await expect(c.getByTestId('update-download-line')).toHaveText(
    'Downloaded and verified: BaiheStudio-Setup-0.2.0.exe.',
  )
  await expect(c.getByRole('button', { name: 'Download' })).toHaveCount(0)

  // Nothing is installed on the first press: it asks first.
  await c.getByRole('button', { name: /Install and restart/ }).click()
  expect(calls.filter((x) => x.path.endsWith('/install'))).toHaveLength(0)
  await c.getByRole('button', { name: 'Open Setup for version 0.2.0' }).click()
  await expect(c).toContainText('Setup is open (BaiheStudio-Setup-0.2.0.exe, version 0.2.0).')
  const install = calls.filter((x) => x.path.endsWith('/install'))
  expect(install).toEqual([{ method: 'POST', path: '/api/system/update/install', body: { confirm: true } }])
})

test('the daily check is a switch, off by default', async ({ page }) => {
  const calls = await mockUpdates(page)
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'Preferences')
  const toggle = card(page).getByRole('switch', { name: 'Check once a day' })
  await toggle.click()
  await expect(toggle).toBeChecked()
  expect(calls.find((x) => x.path.endsWith('/settings'))?.body).toEqual({ auto_check: true })
  expect(calls.some((x) => x.path.endsWith('/download') || x.path.endsWith('/check'))).toBe(false)
})

test('a custom update source is named on the card', async ({ page }) => {
  await mockUpdates(page, { custom_source: true })
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'Preferences')
  await expect(card(page).getByTestId('update-custom-source')).toHaveText(
    'Custom update source: this PC checks a different repository.',
  )
})

test('away from the PC the card says so and asks nothing', async ({ page }) => {
  const calls = await mockUpdates(page)
  await page.route((u) => u.pathname === '/api/meta', (route) =>
    json(route, { app: 'Baihe Studio', api_version: '0.1', environment: 'production', local: false }),
  )
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'Preferences')
  const c = card(page)
  await expect(c).toContainText('Run this on the main PC.')
  await expect(c.getByRole('button', { name: 'Check for updates' })).toHaveCount(0)
  expect(calls).toEqual([])
})
