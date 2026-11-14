import { expect, test } from './fixtures'
import { openTab } from './reviewFolds'
import { clearDrama3Lines, clearLines, clearReviewResults, seedOneLine, seedReviewResults } from './reviewResultsSeed'

// The Review tools under the line list: one tab level of four groups (the shared
// Tabs), the last group remembered per title, a link opening a group, and each
// group about one desktop screen tall.

test.beforeEach(() => seedReviewResults())
test.afterAll(() => {
  clearReviewResults()
  clearLines(1)
})

const TABS = ['Look for problems', 'Change many lines', 'Records', 'Finish']
const tablist = (page: import('@playwright/test').Page) => page.getByRole('tablist', { name: 'Review tools' })

test('one tab level with the four groups; Look for problems holds the four checks as a 2x2 grid with an inline Bulk', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.goto('/#/drama/3/review')
  await expect(tablist(page)).toBeVisible()
  await expect(page.getByRole('tablist', { name: 'Review tools' }).getByRole('tab')).toHaveCount(4)
  for (const name of TABS) await expect(tablist(page).getByRole('tab', { name: new RegExp(`^${name}`) })).toBeVisible()
  await expect(tablist(page).getByRole('tab', { name: /^Look for problems/ })).toHaveAttribute('aria-selected', 'true')

  const boxes = await page.getByTestId(/^review-job-/).evaluateAll((els) => els.map((e) => e.getBoundingClientRect()))
  expect(boxes).toHaveLength(4)
  expect(new Set(boxes.map((b) => Math.round(b.top))).size).toBe(2)
  expect(new Set(boxes.map((b) => Math.round(b.left))).size).toBe(2)
  const row = page.getByTestId('review-job-consistency')
  await expect(row.getByRole('switch', { name: 'Bulk: Check consistency' })).toBeVisible()
  await expect(row.getByRole('button', { name: 'Help: Bulk' })).toBeVisible()
})

test('each group fits about one desktop screen, with no sideways scroll, light and dark', async ({ page }) => {
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

test('the last group is remembered per title and a link opens a group', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  await openTab(page, 'Records')
  await page.reload()
  await expect(tablist(page).getByRole('tab', { name: /^Records/ })).toHaveAttribute('aria-selected', 'true')
  // Another title has its own memory (the panel only shows on a title that has lines).
  seedOneLine(1)
  await page.goto('/#/drama/1/review')
  await expect(tablist(page).getByRole('tab', { name: /^Look for problems/ })).toHaveAttribute('aria-selected', 'true')
  // A link wins over the memory.
  await page.goto('/#/drama/3/review?tools=finish')
  await expect(tablist(page).getByRole('tab', { name: /^Finish/ })).toHaveAttribute('aria-selected', 'true')
})

test('a tab remembered or linked from before the grouping opens the group that holds its tools', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  await expect(tablist(page)).toBeVisible()
  await page.evaluate(() => window.localStorage.setItem('baihe.pref.review.tools.tab.3', JSON.stringify('restructure')))
  await page.reload()
  await expect(tablist(page).getByRole('tab', { name: /^Change many lines/ })).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('.review-toolbox-panel:not([hidden])').locator('summary', { hasText: 'Structure' }).first()).toBeVisible()
  for (const [old, group, tool] of [['history', 'Records', 'Edit tendencies'], ['coverage', 'Look for problems', 'Coverage and pacing'], ['extras', 'Look for problems', 'Audio tags (SenseVoice)']]) {
    await page.goto('about:blank')
    await page.goto(`/#/drama/3/review?tools=${old}`)
    await expect(tablist(page).getByRole('tab', { name: new RegExp(`^${group}`) }), old).toHaveAttribute('aria-selected', 'true')
    await expect(page.locator('.review-toolbox-panel:not([hidden])').locator('summary', { hasText: tool }).first(), old).toBeVisible()
  }
})

test('Look for problems carries the Coverage count when there is something to look at', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  await expect(tablist(page).getByRole('tab', { name: /^Look for problems/ }).getByText(/to look at/)).toBeAttached()
  await expect(tablist(page).getByText(/to look at/)).toHaveCount(1)
})

test('a group with nothing in it says so once', async ({ page }) => {
  clearDrama3Lines()
  // No lines, so the tools panel only shows when a link asks for it.
  await page.goto('/#/drama/3/review?tools=finish')
  await openTab(page, 'Finish')
  await expect(page.getByTestId('review-tab-empty')).toBeVisible()
  await expect(page.getByTestId('review-tab-empty')).toHaveCount(1)
})

test('the first card of a group is open the first time, the rest are closed, and a choice is remembered', async ({ page }) => {
  const card = (title: string) =>
    page.locator('details.section').filter({ has: page.locator(':scope > summary .section-title', { hasText: new RegExp(`^${title}$`) }) })
  await page.goto('/#/drama/3/review')
  await openTab(page, 'Finish')
  await expect(card('Burned subtitle preview')).toHaveAttribute('open', '')
  await expect(card('Mark episode reviewed')).not.toHaveAttribute('open', '')
  await card('Burned subtitle preview').locator(':scope > summary').click()
  // Section saves the choice from the <details> toggle event, which fires in a later task than the
  // click; reloading before it runs would lose the choice, so wait for the stored value.
  await page.waitForFunction(() => window.localStorage.getItem('baihe.section.review.aiExtras.burn') === '0')
  await page.reload()
  await openTab(page, 'Finish')
  await expect(card('Burned subtitle preview')).not.toHaveAttribute('open', '')
})

test('no group shows more than its first card open by default', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.goto('/#/drama/3/review')
  await expect(tablist(page)).toBeVisible()
  for (const name of TABS) {
    await openTab(page, name)
    const open = page.locator('.review-toolbox-panel:not([hidden]) details.section[open]:not(details.section details.section)')
    expect(await open.count(), `${name}: open cards`).toBeLessThanOrEqual(1)
  }
})

test('from 1400px the tools sit beside the lines as a sticky inspector; below that they stay underneath', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/#/drama/3/review')
  await expect(tablist(page)).toBeVisible()
  const lines = (await page.locator('.review-editor').boundingBox())!
  const tools = (await page.locator('.review-ai').boundingBox())!
  expect(tools.x).toBeGreaterThanOrEqual(lines.x + lines.width)
  expect(await page.locator('.review-ai').evaluate((el) => getComputedStyle(el).position)).toBe('sticky')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)

  await page.setViewportSize({ width: 1280, height: 800 })
  const lines2 = (await page.locator('.review-editor').boundingBox())!
  const tools2 = (await page.locator('.review-ai').boundingBox())!
  expect(tools2.y).toBeGreaterThanOrEqual(lines2.y + lines2.height - 1)
})
