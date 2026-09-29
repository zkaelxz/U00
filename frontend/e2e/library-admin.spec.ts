import { expect, test, type Page } from '@playwright/test'

// Library admin (selection bar, Backup & storage, PC-only deletes).
// Real API for the bulk flow on two dramas this spec creates and deletes
// (the seeded three stay untouched); page.route mocks for job states,
// restore errors, remote mode and presets.

async function createDrama(page: Page, title: string): Promise<number> {
  const r = await page.request.post('/api/dramas', { data: { source_language: 'zh', title_en: title } })
  expect(r.ok()).toBeTruthy()
  return (await r.json()).id
}

test.describe('selection bar (real API)', () => {
  const ids: number[] = []

  test.afterAll(async ({ request }) => {
    for (const id of ids) await request.delete(`/api/dramas/${id}?confirm=true&confirm_text=DELETE`)
  })

  test('select two, set status, translate needs aligned, typed DELETE removes both', async ({ page }) => {
    ids.push(await createDrama(page, 'Admin E2E One'), await createDrama(page, 'Admin E2E Two'))
    await page.goto('/')
    await page.getByLabel('Search title or summary').fill('Admin E2E')
    await expect(page.getByTestId('drama-count')).toHaveText('2 drama(s)')

    await page.getByRole('checkbox', { name: 'Select Admin E2E One' }).check()
    await page.getByRole('checkbox', { name: 'Select Admin E2E Two' }).check()
    const bar = page.getByRole('region', { name: 'Selection' })
    await expect(bar.getByTestId('selected-count')).toHaveText('2 selected')
    // Clicking a checkbox does not open the row's detail panel.
    await expect(page.getByRole('region', { name: 'Admin E2E One' })).toHaveCount(0)

    await bar.getByLabel('New status').selectOption('translated')
    await bar.getByRole('button', { name: 'Apply' }).click()
    await expect(bar.getByTestId('bulk-result')).toHaveText('Updated 2.')
    await expect(page.locator('tbody tr', { hasText: 'Admin E2E' }).filter({ hasText: 'translated' })).toHaveCount(2)

    await expect(bar.getByRole('button', { name: 'Translate 0' })).toBeDisabled()
    await expect(bar).toContainText("Still needed: a selected drama with status 'aligned'.")

    await bar.getByRole('button', { name: 'Delete…' }).click()
    await expect(bar).toContainText('Permanently deletes 2 dramas with their lines and files. No undo.')
    const confirm = bar.getByRole('button', { name: 'Delete 2' })
    await bar.getByLabel(/Type DELETE to confirm/).fill('delete')
    await expect(confirm).toBeDisabled()
    await bar.getByLabel(/Type DELETE to confirm/).fill('DELETE')
    await confirm.click()
    await expect(page.getByTestId('drama-count')).toHaveText('0 drama(s)')
    await expect(page.getByRole('region', { name: 'Selection' })).toHaveCount(0)
  })
})

test.describe('Backup & storage (mocked)', () => {
  async function openSection(page: Page) {
    await page.goto('/')
    await page.getByText('Backup & storage', { exact: true }).click()
  }

  test('export job reaching done shows the download link', async ({ page }) => {
    let polls = 0
    await page.route('**/api/library/admin/export', (r) =>
      r.fulfill({ json: { job_id: 'library_export_zip', drama_ids: [1] } }))
    await page.route('**/api/jobs/library_export_zip', (r) => {
      polls += 1
      if (polls === 1) return r.fulfill({ status: 404, json: { error: { code: 'not_found', message: 'No job.' } } })
      const status = polls < 3 ? 'running' : 'done'
      return r.fulfill({ json: {
        job_id: 'library_export_zip', status, progress: status === 'done' ? 1 : 0.4, message: '', error: null,
        description: null, gpu_touching: false, started_at: 1, finished_at: null, updated_at: 1,
      } })
    })
    await page.route('**/api/library/admin/artifacts/export/info', (r) =>
      polls >= 3
        ? r.fulfill({ json: { kind: 'export', name: 'dramas_export_20260929.zip', size: 2048 } })
        : r.fulfill({ status: 404, json: { error: { code: 'not_found', message: 'No artifact available.' } } }))
    await openSection(page)
    await page.getByRole('button', { name: /Export all translated/ }).click()
    const link = page.getByTestId('download-export')
    await expect(link).toHaveText('Download dramas_export_20260929.zip')
    await expect(link).toHaveAttribute('href', '/api/library/admin/artifacts/export')
  })

  test('restore is blocked while a job runs, and a 422 shows the server text', async ({ page }) => {
    let jobRunning = true
    await page.route('**/api/jobs', (r) => r.fulfill({ json: {
      count: 1,
      items: [{ job_id: 'translate_1', status: jobRunning ? 'running' : 'done', progress: 0.5, message: '',
        error: null, description: null, gpu_touching: false, started_at: 1, finished_at: null, updated_at: 1 }],
    } }))
    let restoreHeaders: Record<string, string> = {}
    await page.route('**/api/library/admin/restore', (r) => {
      restoreHeaders = r.request().headers()
      return r.fulfill({ status: 422, json: { error: { code: 'validation_error',
        message: 'That file is not a Baihe library backup.' } } })
    })
    await openSection(page)
    await page.getByLabel('Backup file', { exact: true }).setInputFiles({ name: 'b.zip', mimeType: 'application/zip', buffer: Buffer.from('PK') })
    await page.getByLabel(/Type RESTORE to confirm/).fill('RESTORE')
    const restore = page.getByRole('button', { name: 'Restore', exact: true })
    await expect(page.getByText('Wait for the running job to finish.')).toBeVisible()
    await expect(restore).toBeDisabled()

    jobRunning = false
    await expect(restore).toBeEnabled({ timeout: 8000 })
    await restore.click()
    await expect(page.getByText('That file is not a Baihe library backup.')).toBeVisible()
    expect(restoreHeaders['x-baihe-local']).toBe('1')
  })

  test('scan then clean needs CLEAN; changing the preset clears the scan', async ({ page }) => {
    let cleanBody: unknown = null
    await page.route('**/api/library/admin/storage?*', (r) => r.fulfill({ json: {
      preset: 'balanced', categories_to_clean: ['temp_files'], total_bytes: 48.2e9, reclaimable_bytes: 3.1e9,
      would_free_bytes: 2.4e9, per_drama: [],
      categories: [
        { key: 'temp_files', label: 'Temporary files', note: '', bytes: 2.4e9, selected: true },
        { key: 'dub_clips', label: 'Dub clips', note: '', bytes: 0.7e9, selected: false },
      ],
    } }))
    await page.route('**/api/library/admin/storage/clean', (r) => {
      cleanBody = r.request().postDataJSON()
      return r.fulfill({ json: { preset: 'balanced', freed_bytes: 2.3e9,
        results: [{ drama_id: 1, ok: true }, { drama_id: 2, ok: false, error: 'job_running' }] } })
    })
    await openSection(page)
    const cleanUp = page.getByRole('button', { name: 'Clean up…' })
    await expect(cleanUp).toBeDisabled()
    await page.getByRole('button', { name: 'Scan' }).click()
    await expect(page.getByTestId('storage-scan')).toHaveText(
      'Library 48.2 GB · 3.1 GB reclaimable · this preset frees 2.4 GB')
    await expect(page.getByText('Dub clips: 700.0 MB kept')).toBeVisible()

    await page.getByLabel('Cleanup preset', { exact: true }).selectOption('minimal')
    await expect(page.getByTestId('storage-scan')).toHaveCount(0)
    await expect(cleanUp).toBeDisabled()
    await page.getByLabel('Cleanup preset', { exact: true }).selectOption('balanced')

    await page.getByRole('button', { name: 'Scan' }).click()
    await cleanUp.click()
    const go = page.getByRole('button', { name: 'Clean up', exact: true })
    await page.getByLabel(/Type CLEAN to confirm/).fill('clean')
    await expect(go).toBeDisabled()
    await page.getByLabel(/Type CLEAN to confirm/).fill('CLEAN')
    await go.click()
    await expect(page.getByText('Freed 2.3 GB. 1 drama skipped (job running).')).toBeVisible()
    expect(cleanBody).toEqual({ preset: 'balanced', confirm: true, confirm_text: 'CLEAN' })
  })

  test('remote meta: section says main PC with no buttons; bar has no Delete or Export', async ({ page }) => {
    await page.route('**/api/meta', (r) => r.fulfill({ json: {
      app: 'Baihe Studio', api_version: '0.1', environment: 'production', local: false } }))
    await openSection(page)
    await expect(page.getByText('Run this on the main PC.')).toBeVisible()
    await expect(page.getByRole('button', { name: /Back up library|Scan|Export all/ })).toHaveCount(0)
    await page.getByRole('checkbox', { name: 'Select Signal' }).check()
    const bar = page.getByRole('region', { name: 'Selection' })
    await expect(bar.getByTestId('selected-count')).toHaveText('1 selected')
    await expect(bar.getByRole('button', { name: 'Delete…' })).toHaveCount(0)
    await expect(bar.getByRole('button', { name: /Export \.zip/ })).toHaveCount(0)
    await expect(bar).toContainText('Delete and export are PC only.')
  })
})

test('preset delete is two-step: first press makes no call, second sends confirm', async ({ page }) => {
  const calls: unknown[] = []
  let deleted = false
  await page.route('**/api/library/presets', (r) => r.fulfill({ json: {
    items: deleted ? [] : [{ id: 7, name: 'Wuxia preset', translation_engine: 'claude', engine_model: null,
      style_preset: null, locale: null }],
  } }))
  await page.route('**/api/library/presets/7/delete', (r) => {
    calls.push(r.request().postDataJSON())
    deleted = true
    return r.fulfill({ json: { preset_id: 7, deleted: true } })
  })
  await page.goto('/')
  await page.getByText(/^Presets \(1\)/).click()
  await expect(page.getByText('Dramas that used it keep their settings.')).toBeVisible()

  await page.getByRole('button', { name: 'Delete Wuxia preset' }).click()
  const confirm = page.getByRole('button', { name: 'Confirm delete Wuxia preset' })
  await expect(confirm).toBeVisible()
  expect(calls).toHaveLength(0)
  await page.keyboard.press('Escape')
  await expect(confirm).toHaveCount(0)

  await page.getByRole('button', { name: 'Delete Wuxia preset' }).click()
  await page.getByRole('button', { name: 'Confirm delete Wuxia preset' }).click()
  await expect.poll(() => calls).toEqual([{ confirm: true }])
  await expect(page.getByText(/^Presets \(/)).toHaveCount(0)
})

test('an armed delete reverts after 5 s', async ({ page }) => {
  await page.route('**/api/library/voice-bank', (r) => r.fulfill({ json: {
    items: [{ id: 3, name: 'Narrator', language: 'en', clone_engine: null, source_drama: null, clip_available: false }],
  } }))
  await page.goto('/')
  await page.getByText(/^Voice bank \(1\)/).click()
  await page.getByRole('button', { name: 'Delete Narrator' }).click()
  await expect(page.getByRole('button', { name: 'Confirm delete Narrator' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Confirm delete Narrator' })).toHaveCount(0, { timeout: 7000 })
  await expect(page.getByRole('button', { name: 'Delete Narrator' })).toBeVisible()
})
