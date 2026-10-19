import { expect, test, type Page } from '@playwright/test'

const STATS = {
  by_status: {}, by_media_type: {}, total_lines: 0, translated_lines: 0,
  usage: { input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, estimated_cost_usd: 0, call_count: 0 },
}

async function emptyLibrary(page: Page) {
  await page.route('**/api/library/dramas', (r) => r.fulfill({ json: { items: [], count: 0 } }))
  await page.route('**/api/library/stats', (r) => r.fulfill({ json: { ...STATS, total_dramas: 0 } }))
}

test.describe('a browser that never chose', () => {
  // The config starts every spec from a saved floating nav; this one is the newcomer.
  test.use({ storageState: { cookies: [], origins: [] } })

  test('sees the labelled rail on a wide screen', async ({ page }) => {
    await page.goto('/#/jobs')
    const rail = page.locator('.app-rail')
    await expect(rail).toHaveClass(/is-docked/)
    await expect(rail.getByText('Library', { exact: true })).toBeVisible()
  })

  test('keeps a saved floating choice', async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('baihe.navDock', 'float'))
    await page.goto('/#/jobs')
    await expect(page.locator('.app-rail')).not.toHaveClass(/is-docked/)
  })
})

test('an empty Library has one primary button, says "No titles yet" once and shows no search bar', async ({ page }) => {
  await emptyLibrary(page)
  await page.goto('/#/')
  const main = page.locator('.library-page')
  await expect(main.getByText('No titles yet')).toHaveCount(1)
  await expect(main.locator('.btn-primary:visible')).toHaveCount(1)
  await expect(main.getByRole('link', { name: 'Make subtitles' })).toHaveClass(/btn-primary/)
  await expect(main.getByRole('button', { name: 'New title' })).toHaveClass(/btn-secondary/)
  await expect(page.getByRole('search')).toBeHidden()
  await page.getByRole('button', { name: 'Hide' }).first().click()
  await expect(main.locator('.btn-primary:visible')).toHaveCount(1)
  await expect(main.getByText('No titles yet')).toHaveCount(1)
})

test('the search bar is there once the Library has a title', async ({ page }) => {
  await page.goto('/#/')
  await expect(page.getByRole('search')).toBeVisible()
})
