import { expect, test } from '@playwright/test'

import { ME } from './authMocks'
import { CONFLICT, mockLibrarySharing } from './librarySharingMocks'

// Library: the per-item sharing control for the signed-in owner (desktop).

test('owner sees Private/Shared on their own items and can change them', async ({ page }) => {
  const s = await mockLibrarySharing(page, ME.signedIn)
  await page.goto('/')
  const hidden = page.locator('li.drama-card', { hasText: 'Hidden Letters' })
  await expect(hidden.getByText('Private', { exact: true })).toBeVisible()

  // Someone else's item: no badge, no control.
  const other = page.locator('li.drama-card', { hasText: 'Neighbour Tale' })
  await expect(other.locator('.sharing-control')).toHaveCount(0)

  await hidden.getByRole('button', { name: 'Share with household: Hidden Letters' }).click()
  await expect(hidden.getByText('Shared', { exact: true })).toBeVisible()
  await expect(hidden.getByRole('button', { name: 'Make private: Hidden Letters' })).toBeVisible()

  // A drama in a series only explains.
  const ep = page.locator('li.drama-card', { hasText: 'Saga Ep 1' })
  await expect(ep.getByRole('button', { name: /Make private|Share with household/ })).toHaveCount(0)
  await expect(ep).toContainText('sharing is set for the whole series')

  // The series control (on Library tools) shows the server's 409 as is.
  await page.goto('/#/library-tools')
  const tools = page.getByRole('region', { name: 'Library tools' })
  await tools.getByRole('button', { name: 'Make private: Saga' }).click()
  await expect(tools.getByRole('alert')).toHaveText(CONFLICT)
  await expect(tools.getByText('Shared', { exact: true })).toBeVisible()

  expect(s.posts).toEqual([
    { path: '/api/sharing/dramas/1/private', body: { private: false } },
    { path: '/api/sharing/series/9/private', body: { private: true } },
  ])
  expect(s.unmocked).toEqual([])
})

test('no control with sign-in off', async ({ page }) => {
  const s = await mockLibrarySharing(page, ME.authOff)
  await page.goto('/')
  await expect(page.locator('li.drama-card', { hasText: 'Hidden Letters' })).toBeVisible()
  await expect(page.locator('.sharing-control')).toHaveCount(0)
  expect(s.unmocked).toEqual([])
})

// A household member holds no admin.library: the server refuses these writes, so the page must
// not offer them. The server still enforces; this is only the UX.
test.describe('member without admin.library', () => {
  test('no New title, bulk status or Rename', async ({ page }) => {
    await mockLibrarySharing(page, ME.signedIn)
    await page.route('**/api/library/presets', (r) => r.fulfill({ json: { items: [{ id: 4, name: 'Fast draft', translation_engine: 'claude' }] } }))
    await page.goto('/')
    await expect(page.locator('li.drama-card', { hasText: 'Hidden Letters' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'New title' })).toHaveCount(0)

    await page.getByRole('button', { name: 'Select', exact: true }).click()
    await page.getByRole('checkbox', { name: 'Select Hidden Letters' }).check()
    const bar = page.getByRole('region', { name: 'Selection' })
    await expect(bar.getByText('Changing status and lists is for the library admin.')).toBeVisible()
    await expect(bar.getByRole('button', { name: 'Set status' })).toHaveCount(0)
    await expect(bar.getByRole('button', { name: 'Add to list' })).toHaveCount(0)
    await expect(bar.getByRole('button', { name: 'Remove from list' })).toHaveCount(0)

    await page.goto('/#/library-tools')
    await page.getByRole('group', { name: /Presets/ }).or(page.locator('summary', { hasText: 'Presets' })).first().click()
    await expect(page.getByText('Fast draft')).toBeVisible()
    await expect(page.getByRole('button', { name: /^Rename/ })).toHaveCount(0)
    await expect(page.getByTestId('no-rename-note').first()).toBeVisible()
  })

  test('an empty library points to Sources and Discover, not New title', async ({ page }) => {
    await mockLibrarySharing(page, ME.signedIn)
    await page.route('**/api/library/dramas', (r) => r.fulfill({ json: { items: [], count: 0 } }))
    await page.route('**/api/library/stats', (r) => r.fulfill({ json: {
      total_dramas: 0, by_status: {}, by_media_type: {}, total_lines: 0, translated_lines: 0,
      usage: { input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, estimated_cost_usd: 0, call_count: 0 },
    } }))
    await page.goto('/')
    await page.getByRole('button', { name: 'Dismiss' }).first().click()
    await expect(page.getByTestId('member-empty-note').getByRole('link', { name: 'Sources' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'New title' })).toHaveCount(0)
  })

  test('a failed Continue load shows a banner', async ({ page }) => {
    await mockLibrarySharing(page, ME.signedIn)
    await page.route('**/api/library/continue', (r) => r.fulfill({ status: 500, json: { error: { code: 'internal_error', message: 'Boom.' } } }))
    await page.goto('/')
    await expect(page.getByRole('alert').first()).toBeVisible()
  })
})
