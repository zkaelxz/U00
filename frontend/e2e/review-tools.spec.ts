import { expect, test } from './fixtures'
import { openTab } from './reviewFolds'
import { clearDrama3Lines, clearReviewResults, seedReviewResults } from './reviewResultsSeed'

// The Review tools under the line list: one tab level (the shared Tabs), the
// last tab remembered per title, a link opening a tab, and each tab about one
// desktop screen tall.

test.beforeEach(() => seedReviewResults())
test.afterAll(() => clearReviewResults())

const TABS = ['Check', 'Fix', 'Coverage', 'Restructure', 'History', 'Extras']
const tablist = (page: import('@playwright/test').Page) => page.getByRole('tablist', { name: 'Review tools' })

test('one tab level with the six tabs; Check holds the four checks as a 2x2 grid with an inline Bulk', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.goto('/#/drama/3/review')
  await expect(tablist(page)).toBeVisible()
  await expect(page.getByRole('tablist', { name: 'Review tools' }).getByRole('tab')).toHaveCount(6)
  for (const name of TABS) await expect(tablist(page).getByRole('tab', { name: new RegExp(`^${name}`) })).toBeVisible()
  await expect(tablist(page).getByRole('tab', { name: /^Check/ })).toHaveAttribute('aria-selected', 'true')

  const boxes = await page.getByTestId(/^review-job-/).evaluateAll((els) => els.map((e) => e.getBoundingClientRect()))
  expect(boxes).toHaveLength(4)
  expect(new Set(boxes.map((b) => Math.round(b.top))).size).toBe(2)
  expect(new Set(boxes.map((b) => Math.round(b.left))).size).toBe(2)
  const row = page.getByTestId('review-job-consistency')
  await expect(row.getByRole('switch', { name: 'Bulk: Check consistency' })).toBeVisible()
  await expect(row.getByRole('button', { name: 'Help: Bulk' })).toBeVisible()
})

test('each tab fits about one desktop screen, with no sideways scroll, light and dark', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme })
    await page.goto('about:blank')
    await page.goto('/#/drama/3/review')
    await expect(tablist(page)).toBeVisible()
    for (const name of TABS) {
      await openTab(page, name)
      const panel = page.locator('.review-toolbox-panel:not([hidden])')
      expect((await panel.boundingBox())!.height, `${name} panel height`).toBeLessThanOrEqual(800)
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `${name} sideways`).toBe(true)
    }
  }
})

test('the last tab is remembered per title and a link opens a tab', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  await openTab(page, 'Coverage')
  await page.reload()
  await expect(tablist(page).getByRole('tab', { name: /^Coverage/ })).toHaveAttribute('aria-selected', 'true')
  // Another title has its own memory.
  await page.goto('/#/drama/1/review')
  await expect(tablist(page).getByRole('tab', { name: /^Check/ })).toHaveAttribute('aria-selected', 'true')
  // A link wins over the memory.
  await page.goto('/#/drama/3/review?tools=history')
  await expect(tablist(page).getByRole('tab', { name: /^History/ })).toHaveAttribute('aria-selected', 'true')
})

test('Coverage carries a count when there is something to look at', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  await expect(tablist(page).getByRole('tab', { name: /^Coverage/ }).getByText(/to look at/)).toBeAttached()
})

test('a tab with nothing in it says so once', async ({ page }) => {
  clearDrama3Lines()
  await page.goto('/#/drama/3/review')
  await openTab(page, 'Restructure')
  await expect(page.getByTestId('review-tab-empty')).toBeVisible()
  await expect(page.getByTestId('review-tab-empty')).toHaveCount(1)
})

test('the first card of a tab is open the first time, the rest are closed, and a choice is remembered', async ({ page }) => {
  const card = (title: string) =>
    page.locator('details.section').filter({ has: page.locator(':scope > summary .section-title', { hasText: new RegExp(`^${title}$`) }) })
  await page.goto('/#/drama/3/review')
  await openTab(page, 'Restructure')
  await expect(card('Structure')).toHaveAttribute('open', '')
  await expect(card('Merge short lines')).not.toHaveAttribute('open', '')
  await card('Structure').locator(':scope > summary').click()
  await page.reload()
  await openTab(page, 'Restructure')
  await expect(card('Structure')).not.toHaveAttribute('open', '')
})

test('no tab shows more than its first card open by default', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.goto('/#/drama/3/review')
  await expect(tablist(page)).toBeVisible()
  for (const name of TABS) {
    await openTab(page, name)
    const open = page.locator('.review-toolbox-panel:not([hidden]) details.section[open]:not(details.section details.section)')
    expect(await open.count(), `${name}: open cards`).toBeLessThanOrEqual(1)
  }
})
