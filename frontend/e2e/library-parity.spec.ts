import { expect, test, type Page } from '@playwright/test'

import { navLink, openMenu } from './settingsNav'

// Library parity (inventory L02, L07, L15). Filters run against the real
// seeded API (e2e/serve_seeded_api.py: three dramas); the Continue shelf and
// the history clear are mocked so the shared library is left as it was.

// The Library's own drama list, so a link or button elsewhere on the page
// (Continue shelf, Library tools) never matches.
const dramas = (page: Page) => page.getByRole('region', { name: 'Dramas' })
const tools = (page: Page) => page.getByRole('region', { name: 'Library tools' })

test('More filters: language, author and custom tags go through the API', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('drama-count')).toHaveText('3 titles')
  await page.getByText('More filters', { exact: true }).click()
  const more = page.locator('.more-filters')

  await more.getByLabel('Language', { exact: true }).selectOption('ko')
  await expect(page.getByTestId('drama-count')).toHaveText('1 title')
  await expect(dramas(page).getByRole('link', { name: 'Signal', exact: true })).toBeVisible()
  await expect(page.getByText('More filters (1)')).toBeVisible()
  await page.getByRole('button', { name: 'Clear these filters' }).click()
  await expect(page.getByTestId('drama-count')).toHaveText('3 titles')

  await expect(more.getByLabel('Author', { exact: true }).locator('option', { hasText: '墨香铜臭' })).toHaveCount(1)
  await more.getByLabel('Author', { exact: true }).selectOption('墨香铜臭')
  await expect(page.getByTestId('drama-count')).toHaveText('1 title')
  await more.getByLabel('Author', { exact: true }).selectOption('')

  await page.getByRole('checkbox', { name: 'wuxia' }).check()
  await expect(page.getByTestId('drama-count')).toHaveText('1 title')
  await expect(dramas(page).getByRole('link', { name: 'Grandmaster of Demonic Cultivation', exact: true })).toBeVisible()
  await page.getByRole('checkbox', { name: 'wuxia' }).uncheck()
  await expect(page.getByTestId('drama-count')).toHaveText('3 titles')
})

test('a More filters choice with no match: Clear filters resets it too', async ({ page }) => {
  await page.goto('/')
  await page.getByText('More filters', { exact: true }).click()
  await page.getByRole('checkbox', { name: 'wuxia' }).check()
  await page.locator('.more-filters').getByLabel('Language', { exact: true }).selectOption('ko')
  await expect(page.getByText('No titles match.')).toBeVisible()
  await page.getByRole('button', { name: 'Clear filters', exact: true }).click()
  await expect(page.getByTestId('drama-count')).toHaveText('3 titles')
  await expect(page.getByText('More filters', { exact: true })).toBeVisible()
  await expect(page.getByRole('checkbox', { name: 'wuxia' })).not.toBeChecked()
})

test('Continue resumes reading at the saved page (GET /api/library/continue)', async ({ page }) => {
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
  // No workspace activity, so the reading entry is the only one.
  await page.route('**/api/library/recent', (r) => r.fulfill({ json: { items: [] } }))
  await page.goto('/')
  const shelf = page.getByRole('region', { name: 'Continue' })
  await expect(shelf.getByRole('listitem')).toHaveCount(1)
  await expect(shelf).toContainText('Reading · page 2 · 42%')
  await expect(shelf.getByRole('link', { name: "Resume reading Heaven Official's Blessing" })).toHaveAttribute('href', '#/read/2')
})

test('Reading history Clear is a two-step PC-only action inside Library tools', async ({ page }) => {
  let history = { items: [{ drama_id: 1, line_idx: 3, percent_complete: 10, accessed_at: '2026-09-29T12:00:00', title_en: 'Grandmaster of Demonic Cultivation', title_zh: null }] }
  const bodies: unknown[] = []
  await page.route('**/api/library/history', (r) => r.fulfill({ json: history }))
  await page.route('**/api/library/history/clear', (r) => {
    bodies.push(r.request().postDataJSON())
    history = { items: [] }
    return r.fulfill({ json: { cleared: true, removed: 1 } })
  })
  await page.goto('/#/library-tools')
  await tools(page).locator('summary', { hasText: 'Reading history' }).click()
  const section = tools(page).getByRole('region', { name: 'Reading history' })
  await expect(section.getByRole('listitem')).toHaveCount(1)
  await section.getByRole('button', { name: 'Clear reading history' }).click()
  expect(bodies).toEqual([])
  await section.getByRole('button', { name: 'Confirm clear reading history' }).click()
  await expect(tools(page).locator('summary', { hasText: 'Reading history' })).toHaveCount(0)
  expect(bodies).toEqual([{ confirm: true }])
})

test('Reading history on a remote device: no Clear button', async ({ page }) => {
  await page.route('**/api/meta', (r) => r.fulfill({ json: {
    app: 'Baihe Studio', api_version: '0.1', environment: 'production', local: false } }))
  await page.route('**/api/library/history', (r) => r.fulfill({ json: { items: [
    { drama_id: 1, line_idx: 3, percent_complete: 10, accessed_at: '2026-09-29T12:00:00', title_en: 'Grandmaster of Demonic Cultivation', title_zh: null },
  ] } }))
  await page.goto('/#/library-tools')
  await tools(page).locator('summary', { hasText: 'Reading history' }).click()
  const section = tools(page).getByRole('region', { name: 'Reading history' })
  await expect(section).toContainText('Clearing history is PC only.')
  await expect(section.getByRole('button', { name: 'Clear reading history' })).toHaveCount(0)
})

test('Library header has only New title; Library tools lives in the rail or drawer (no Saved manga item)', async ({ page }) => {
  await page.setViewportSize({ width: 800, height: 900 })
  await page.goto('/')
  const head = page.locator('.page-head')
  await expect(head.getByRole('button', { name: 'New title' })).toBeVisible()
  await expect(head.getByRole('link', { name: 'Saved manga' })).toHaveCount(0)
  await expect(head.getByRole('link', { name: 'Library tools' })).toHaveCount(0)
  await openMenu(page)
  await expect(navLink(page, 'Saved manga')).toHaveCount(0)
  await expect(navLink(page, 'Library tools')).toBeVisible()
  await page.setViewportSize({ width: 1280, height: 900 })
  await expect(navLink(page, 'Saved manga')).toHaveCount(0)
  await expect(navLink(page, 'Library tools')).toBeVisible()
  await expect(head.getByRole('link', { name: 'Saved manga' })).toHaveCount(0)
})

test('Library keeps only a summary and links to Library tools', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('stats')).toBeVisible()
  await expect(page.getByRole('region', { name: 'Library tools' })).toHaveCount(0)
  await expect(page.locator('summary', { hasText: 'Backup & storage' })).toHaveCount(0)
  await page.getByRole('navigation').getByRole('link', { name: 'Library tools', exact: true }).first().click()
  await expect(page.getByRole('heading', { name: 'Library tools', level: 2 })).toBeVisible()
  await expect(tools(page).locator('summary', { hasText: 'Backup & storage' })).toBeVisible()
  await page.getByRole('link', { name: 'Back to Library' }).click()
  await expect(page.getByTestId('drama-count')).toBeVisible()
})
