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
