import { expect, test } from '@playwright/test'

// Library parity (inventory L02, L07, L15). Filters run against the real
// seeded API (e2e/serve_seeded_api.py: three dramas); the Continue strip and
// the history clear are mocked so the shared library is left as it was.

test('More filters: language, author and custom tags go through the API', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('drama-count')).toHaveText('3 drama(s)')
  await page.getByText('More filters', { exact: true }).click()

  await page.locator('.more-filters').getByLabel('Language', { exact: true }).selectOption('ko')
  await expect(page.getByTestId('drama-count')).toHaveText('1 drama(s)')
  await expect(page.getByRole('button', { name: 'Signal' })).toBeVisible()
  await expect(page.getByText('More filters (1)')).toBeVisible()
  await page.getByRole('button', { name: 'Clear these filters' }).click()
  await expect(page.getByTestId('drama-count')).toHaveText('3 drama(s)')

  await expect(page.locator('.more-filters').getByLabel('Author', { exact: true }).locator('option', { hasText: '墨香铜臭' })).toHaveCount(1)
  await page.locator('.more-filters').getByLabel('Author', { exact: true }).selectOption('墨香铜臭')
  await expect(page.getByTestId('drama-count')).toHaveText('1 drama(s)')
  await page.locator('.more-filters').getByLabel('Author', { exact: true }).selectOption('')

  await page.getByRole('checkbox', { name: 'wuxia' }).check()
  await expect(page.getByTestId('drama-count')).toHaveText('1 drama(s)')
  await expect(page.getByRole('button', { name: 'Grandmaster of Demonic Cultivation' })).toBeVisible()
  await page.getByRole('checkbox', { name: 'wuxia' }).uncheck()
  await expect(page.getByTestId('drama-count')).toHaveText('3 drama(s)')
})

test('Continue reading resumes in the Reader', async ({ page }) => {
  await page.route('**/api/library/continue', (r) =>
    r.fulfill({
      json: {
        items: [{
          drama_id: 2, title_en: "Heaven Official's Blessing", title_zh: '天官赐福', percent_complete: 42.4,
          last_page: 2, last_accessed_at: '2026-09-29T12:00:00', has_cover_art: false,
        }],
      },
    }),
  )
  await page.goto('/')
  const strip = page.getByRole('region', { name: 'Continue reading' })
  await expect(strip).toContainText('42% · page 2')
  await expect(strip.getByRole('link', { name: "Resume Heaven Official's Blessing" })).toHaveAttribute('href', '#/read/2')
})

test('Reading history Clear is a two-step PC-only action', async ({ page }) => {
  let history = { items: [{ drama_id: 1, line_idx: 3, percent_complete: 10, accessed_at: '2026-09-29T12:00:00', title_en: 'Grandmaster of Demonic Cultivation', title_zh: null }] }
  const bodies: unknown[] = []
  await page.route('**/api/library/history', (r) => r.fulfill({ json: history }))
  await page.route('**/api/library/history/clear', (r) => {
    bodies.push(r.request().postDataJSON())
    history = { items: [] }
    return r.fulfill({ json: { cleared: true, removed: 1 } })
  })
  await page.goto('/')
  await page.locator('summary', { hasText: 'Reading history' }).click()
  await page.getByRole('button', { name: 'Clear reading history' }).click()
  expect(bodies).toEqual([])
  await page.getByRole('button', { name: 'Confirm clear reading history' }).click()
  await expect(page.locator('summary', { hasText: 'Reading history' })).toHaveCount(0)
  expect(bodies).toEqual([{ confirm: true }])
})
