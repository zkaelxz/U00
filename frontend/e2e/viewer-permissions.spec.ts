import { expect, test } from '@playwright/test'

import { ME } from './authMocks'
import { exportBlock, mockExportReady, openExportBlock } from './exportBlocks'
import { mockDub, mockProgress } from './plainErrorsMocks'

// A household member who may read the library and edit lines (ME.signedIn) opens the Export and Dub stages. The
// workspace does not hide its write controls for a viewer (only the server refuses), so what is pinned here is that
// the refusal reads as one plain sentence, not a raw error or the generic failure text.

const FORBIDDEN = { error: { code: 'forbidden', message: 'Not allowed.' } }
const PLAIN = 'Not allowed from this device or account.'

test.beforeEach(async ({ page }) => {
  await page.route('**/api/auth/me', (route) => route.fulfill({ json: ME.signedIn }))
})

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

test('a 403 on starting an export shows the plain refusal once', async ({ page }) => {
  await mockExportReady(page)
  await mockProgress(page, { has_narration_track: true })
  await page.route('**/api/export/dramas/1/audiobook', (route) => route.fulfill({ status: 403, json: FORBIDDEN }))
  await page.goto('/#/drama/1/export')
  await openExportBlock(page, 'Audiobook')
  await exportBlock(page, 'Audiobook').getByRole('button', { name: 'Start audiobook export' }).click()
  await expect(page.getByRole('alert').filter({ hasText: PLAIN })).toHaveCount(1)
  await expect(page.getByText('Something went wrong')).toHaveCount(0)
})

test('a 403 on Generate dub shows the plain refusal', async ({ page }) => {
  await mockDub(page, null)
  await page.route('**/api/dub/dramas/1/run', (route) => route.fulfill({ status: 403, json: FORBIDDEN }))
  await page.goto('/#/drama/1/dub')
  await page.getByRole('button', { name: 'Generate dub' }).click()
  await expect(page.getByRole('alert').filter({ hasText: PLAIN })).toBeVisible()
  await expect(page.getByText('not installed or not reachable')).toHaveCount(0)
})
