import { expect, test } from '@playwright/test'

import { mockDependencyInstall } from './dependencyInstallMock'
import { mockFirstRun } from './getStartedMocks'
import { mockTranscription } from './transcriptionMissingMocks'

// The preflight card: a missing Whisper installs without leaving the screen, a
// failed install shows an error and a retry, a key is added inline, and remote
// viewers get no buttons. Install-presets, setup-checks and the install job are mocked.

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

const card = (page: import('@playwright/test').Page) => page.getByRole('region', { name: 'Before you run' })

test('installs Whisper in place, then Transcribe is available', async ({ page }) => {
  let installed = false
  await mockTranscription(page, () => installed)
  // The answers flip once the fake install job has started, as the real server's do when pip finishes.
  const install = await mockDependencyInstall(page, () => ({ ok: true, output_tail: [] }), { onStart: () => { installed = true } })
  await page.goto('/#/drama/1/source')
  await card(page).getByRole('button', { name: /Install \(about 80 MB\)/ }).click()
  await card(page).getByRole('button', { name: 'Confirm install 1 package (approx. 80 MB)' }).click()
  await expect(card(page)).toHaveCount(0)
  expect(install.started).toHaveLength(1)
  expect(new URL(install.started[0].url()).pathname).toBe('/api/diagnostics/dependencies/faster_whisper/install')
  await expect(page).toHaveURL(/#\/drama\/1\/source/)
  await expect(page.locator('#transcribe-not-installed')).toHaveCount(0)
})

test('a failed install shows an error and a retry', async ({ page }) => {
  await mockTranscription(page, false)
  let ok = false
  await mockDependencyInstall(page, () => ({ ok, output_tail: ['ERROR: /home/x/.cache failed'], hint: 'Pip could not finish.' }))
  await page.goto('/#/drama/1/source')
  await card(page).getByRole('button', { name: /Install \(about 80 MB\)/ }).click()
  await card(page).getByRole('button', { name: /Confirm install/ }).click()
  await expect(card(page).getByRole('alert')).toBeVisible()
  await expect(card(page)).not.toContainText('/home/')
  ok = true
  await expect(card(page).getByRole('button', { name: 'Try again' })).toBeVisible()
})

test('remote viewers see the state but no action buttons', async ({ page }) => {
  await mockTranscription(page, false)
  await page.route('**/api/meta', async (route) => {
    const resp = await route.fetch()
    await route.fulfill({ response: resp, json: { ...(await resp.json()), local: false } })
  })
  await page.goto('/#/drama/1/source')
  await expect(card(page).getByTestId('preflight-whisper')).toContainText('Ask the PC owner')
  await expect(card(page).getByRole('button')).toHaveCount(0)
})

test('a key is added inline in Make subtitles', async ({ page }) => {
  await mockFirstRun(page)
  const posts: unknown[] = []
  await page.route('**/api/settings/keys/claude', (r) => {
    posts.push(r.request().postDataJSON())
    return r.fulfill({ json: { engine: 'claude', configured: true } })
  })
  await page.route('**/api/diagnostics/**', (r) => r.fulfill({ status: 403, json: { code: 'forbidden', message: 'no' } }))
  await page.goto('/')
  await expect(page.getByRole('region', { name: 'Make subtitles' }).getByText('No key saved')).toBeVisible()
  await card(page).getByRole('button', { name: 'Add key' }).click()
  await card(page).getByRole('textbox', { name: 'Claude key' }).fill('not-a-real-key')
  await card(page).getByRole('button', { name: 'Save key' }).click()
  await card(page).getByRole('button', { name: /Confirm: save/ }).click()
  await expect.poll(() => posts.length).toBe(1)
  await expect(card(page).getByText('not-a-real-key')).toHaveCount(0)
})
