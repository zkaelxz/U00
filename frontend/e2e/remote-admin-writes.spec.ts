import { expect, test, type Page } from '@playwright/test'

// A remote (household) admin holds no admin.library: no list of everyone's
// items to flip and Cancel only on their own jobs (owned_by_me). Every /api
// call is mocked; any other is aborted and must not happen.

const me = (permissions: string[]) => ({
  auth_enabled: true, signed_in: true, sign_in_configured: true, zone: 'internet', permissions,
  user: { id: 1, email: 'owner@example.com', display_name: 'Owner', is_admin: true, is_local_owner: false },
})

const JOB = {
  job_id: 'j1', status: 'running', progress: 0.3, message: '', description: 'Translate Kae',
  gpu_touching: false, started_at: 1, finished_at: null, updated_at: 2, owned_by_me: false,
}
const OWN_JOB = { ...JOB, job_id: 'j2', description: 'Translate Mine', owned_by_me: true }
const ITEM = {
  kind: 'drama', id: 5, title: 'Kae drama', owner_name: 'Kae', is_private: true, series_id: null,
  series_name: null, series_is_private: null, created_at_pc: false,
}

async function mock(page: Page, permissions: string[]) {
  const unmocked: string[] = []
  await page.route('**/api/**', (r) => {
    unmocked.push(r.request().url())
    return r.abort()
  })
  const json = (pat: string, body: unknown) => page.route(pat, (r) => r.fulfill({ json: body }))
  await json('**/api/meta', { app: 'Baihe Studio', api_version: '0.1', environment: 'production', local: false })
  await json('**/api/auth/me', me(permissions))
  await json('**/api/jobs', { items: [JOB, OWN_JOB], count: 2 })
  await json('**/api/notifications', { items: [] })
  await json('**/api/sharing/share-by-default', { share_by_default: false })
  await json('**/api/sharing/items*', { items: [ITEM], total: 1 })
  return unmocked
}

test('remote admin: Cancel on their own job only, with a note', async ({ page }) => {
  await mock(page, ['library.read', 'jobs.cancel', 'admin.users.read'])
  await page.goto('/#/diagnostics')
  await expect(page.getByTestId('job-list')).toContainText('Translate Kae')
  await expect(page.getByRole('button', { name: /Cancel Translate Mine/ })).toBeVisible()
  await expect(page.getByRole('button', { name: /Cancel Translate Kae/ })).toHaveCount(0)
  await expect(page.getByTestId('remote-admin-jobs-note')).toBeVisible()
})

test('PC admin still gets Cancel', async ({ page }) => {
  await mock(page, ['library.read', 'jobs.cancel', 'admin.library', 'admin.users.read'])
  await page.goto('/#/diagnostics')
  await expect(page.getByRole('button', { name: /Cancel Translate Kae/ })).toBeVisible()
  await expect(page.getByRole('button', { name: /Cancel Translate Mine/ })).toBeVisible()
  await expect(page.getByTestId('remote-admin-jobs-note')).toHaveCount(0)
})

test('remote admin: no per-item sharing switches in Settings, with a note', async ({ page }) => {
  await mock(page, ['library.read', 'admin.users.read'])
  await page.goto('/#/settings')
  await expect(page.getByTestId('remote-admin-sharing-note')).toBeVisible()
  await expect(page.getByTestId('sharing-drama:5')).toHaveCount(0)
})

test('PC admin still gets every item with its switch', async ({ page }) => {
  await mock(page, ['library.read', 'admin.library'])
  await page.goto('/#/settings')
  await expect(page.getByTestId('sharing-drama:5')).toBeVisible()
  await expect(page.getByTestId('remote-admin-sharing-note')).toHaveCount(0)
})
