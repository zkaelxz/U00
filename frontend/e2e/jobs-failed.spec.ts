import { expect, test } from './fixtures'
import { mockJobsApi } from './jobsMenuMocks'

// A job the server stored as `done` whose outcome is failed must read Failed
// in the Jobs list, the header Jobs menu and the bell, and Cancel must say how
// fast it stops.

const now = () => Math.floor(Date.now() / 1000)

function jobs() {
  const t = now()
  const base = { progress: null, message: '', error: null, gpu_touching: false, updated_at: t, owned_by_me: true, drama_id: null, kind: 'other', page: null }
  return [
    {
      ...base, job_id: 'extract_audio_5', status: 'done', description: 'Audio extraction (drama #5)', drama_id: 5, kind: 'import',
      started_at: t - 120, finished_at: t - 60, outcome: 'failed', outcome_message: 'Separating the vocals failed. Run pip install demucs',
    },
    { ...base, job_id: 'translate_5', status: 'running', progress: 0.3, message: 'Cancelling... (finishes the current step first)', description: 'Translation (drama #5)', started_at: t - 30, finished_at: null, drama_id: 5, kind: 'translate' },
  ]
}

for (const width of [1280, 360]) {
  test(`a failed job reads Failed in the list, the menu and the bell at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 })
    await mockJobsApi(page, jobs(), true)
    await page.route((u) => u.pathname === '/api/notifications', (route) =>
      route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ items: [{ id: 1, at: now() - 60, kind: 'job_failed', text: 'Failed: Audio extraction - Signal: Separating the vocals failed.' }] }),
      }))
    await page.goto('/#/jobs')
    await page.getByRole('button', { name: /^All/ }).click()

    const row = page.getByTestId('job-row-extract_audio_5')
    await expect(row).toContainText('Failed')
    await expect(row).not.toContainText('Done')
    await expect(row).not.toContainText('pip install')
    await expect(row).not.toContainText('drama')
    await expect(page.getByRole('button', { name: /^Failed/ })).toContainText('1')
    await expect(page.getByRole('button', { name: /^Finished/ })).toContainText('0')

    // Cancel stays available and says how fast it stops.
    const cancel = page.getByRole('button', { name: 'Cancel Translation' }).first()
    await expect(cancel).toHaveAttribute('title', /stops after the current AI request \(usually under a minute, up to 5/)
    await expect(page.getByTestId('job-row-translate_5')).toContainText('Cancelling... Stops after the current AI request')

    await page.getByRole('button', { name: /^Jobs/ }).first().click()
    const item = page.locator('.jobs-panel .jobs-item').filter({ hasText: 'Audio extraction' })
    await expect(item).toContainText('Failed')
    await expect(item).not.toContainText('Done')

    await page.getByRole('button', { name: /^Notifications/ }).click()
    const note = page.getByRole('region', { name: 'Recent notifications' }).getByRole('listitem')
    await expect(note).toContainText('Failed')
    await expect(note).not.toContainText('Done')
  })
}
