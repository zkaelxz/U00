import { expect, test, type Page } from '@playwright/test'

import { runClock } from './mockHelpers'
import { sampleJobs } from './jobsMenuMocks'

test('the nav switches between a floating pill, a docked rail and a minimized rail, and remembers it', async ({ page }) => {
  await page.goto('/#/jobs')
  const rail = page.locator('.app-rail')
  await expect(rail).toBeVisible()
  await expect(rail).not.toHaveClass(/is-docked/)
  await page.getByRole('button', { name: 'Dock navigation' }).click()
  await expect(rail).toHaveClass(/is-docked/)
  await expect(rail.getByText('Baihe Studio')).toBeVisible()
  const box = await rail.boundingBox()
  expect(box?.x).toBe(0)
  expect(box?.height).toBeGreaterThanOrEqual(page.viewportSize()!.height - 1)
  await page.getByRole('button', { name: 'Minimize navigation' }).click()
  await expect(rail).toHaveClass(/is-minimized/)
  expect((await rail.boundingBox())?.width).toBeLessThan(80)
  await page.reload()
  await expect(page.locator('.app-rail')).toHaveClass(/is-minimized/)
  await page.getByRole('button', { name: 'Expand navigation' }).click()
  await expect(page.locator('.app-rail')).not.toHaveClass(/is-minimized/)
  await page.getByRole('button', { name: 'Float navigation' }).click()
  await expect(page.locator('.app-rail')).not.toHaveClass(/is-docked/)
})

test('a floating pill that was clicked collapses shortly after the pointer leaves, without a click on the page', async ({ page }) => {
  await page.goto('/#/jobs')
  const rail = page.locator('.app-rail')
  const rest = (await rail.boundingBox())!.width
  await rail.hover()
  await rail.getByRole('link').nth(1).click()
  await expect(rail).toHaveClass(/is-open/)
  await page.mouse.move(700, 400)
  await expect(rail).not.toHaveClass(/is-open/)
  await expect.poll(async () => (await rail.boundingBox())!.width).toBeLessThanOrEqual(rest + 1)
})

test('keyboard focus keeps the floating pill open and tapping outside closes it', async ({ page }) => {
  await page.clock.install()
  await page.goto('/#/jobs')
  const rail = page.locator('.app-rail')
  await rail.getByRole('link').first().focus()
  await page.keyboard.press('Tab')
  await expect(rail).toHaveClass(/is-open/)
  await runClock(page, 5_000)
  await expect(rail).toHaveClass(/is-open/)
  await page.mouse.click(700, 400)
  await expect(rail).not.toHaveClass(/is-open/)
})

// The docked open menu (left or right, not minimized) is a list of words as drawn: no icons, an open title row of its own, a quiet 0 beside Jobs.
const docked = (mode: 'docked' | 'float' | 'minimized') => async ({ page }: { page: Page }) => {
  await page.addInitScript((m) => localStorage.setItem('baihe.navDock', m), mode)
}
const transparent = 'rgba(0, 0, 0, 0)'

test('docked, the menu is 216px wide with the page to its right, and its rows are words with no icon', async ({ page }) => {
  await docked('docked')({ page })
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/#/library')
  const rail = page.locator('.app-rail')
  await expect(rail).toHaveClass(/is-listed/)
  const box = (await rail.boundingBox())!
  // Drawn at a 15px root; the menu is in rem, so it grows with the larger text on a wide window.
  const scale = (await page.evaluate(() => parseFloat(getComputedStyle(document.documentElement).fontSize))) / 15
  expect(Math.round(box.width)).toBe(Math.round(216 * scale))
  // The header is the first thing in the page column: it starts clear of the menu.
  expect((await page.locator('.app-header').boundingBox())!.x).toBeGreaterThanOrEqual(box.width)
  await expect(rail.locator('nav a svg:visible, .rail-toggle svg:visible')).toHaveCount(0)
  await expect(rail.locator('nav a').first()).toBeVisible()
  const mark = (await rail.locator('.rail-brand-mark').boundingBox())!
  expect([Math.round(mark.width), Math.round(mark.height)]).toEqual([30, 30])
  await expect(rail.getByText('Baihe Studio')).toBeVisible()
  // The group names are small, bold and muted; the rows are full-colour text. A group name sits 12px above its first row and 2px over it, set in from the row text by the same 10px.
  const heading = rail.getByRole('heading', { name: 'Find and add' })
  await expect(heading).toHaveCSS('font-weight', '600')
  await expect(heading).toHaveCSS('padding', '12px 10px 2px')
  await expect(heading).toHaveCSS('margin-top', '0px')
})

test('docked, the current row is highlighted: the title row on a title, Library on the library', async ({ page }) => {
  await docked('docked')({ page })
  await page.setViewportSize({ width: 1280, height: 900 })
  const rail = page.getByRole('navigation', { name: 'Main' })
  const library = rail.getByRole('link', { name: 'Library', exact: true })
  await page.goto('/#/drama/2/review')
  const title = page.getByTestId('rail-title')
  await expect(title).toHaveAttribute('aria-current', 'page')
  await expect(title).not.toHaveCSS('background-color', transparent)
  await expect(title).toHaveCSS('font-weight', '600')
  await expect(title.locator('.rail-title-mark')).toBeHidden()
  await expect(library).not.toHaveAttribute('aria-current', 'page')
  await expect(library).toHaveCSS('background-color', transparent)
  await page.goto('/#/library')
  await expect(library).toHaveAttribute('aria-current', 'page')
  await expect(library).not.toHaveCSS('background-color', transparent)
})

test('docked, Jobs shows a quiet 0 that is not part of its name, and the running count once a job runs', async ({ page }) => {
  await docked('docked')({ page })
  await page.setViewportSize({ width: 1280, height: 900 })
  let items: ReturnType<typeof sampleJobs> = []
  await page.route((u) => u.pathname === '/api/jobs', (r) => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items }) }))
  await page.goto('/#/library')
  const jobs = page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Jobs', exact: true })
  await expect(jobs).toBeVisible()
  await expect(jobs.getByTestId('rail-jobs-zero')).toHaveText('0')
  await expect(page.getByTestId('rail-jobs-count')).toHaveCount(0)
  items = sampleJobs().filter((j) => j.status === 'running')
  await page.evaluate(() => window.dispatchEvent(new Event('focus')))
  await expect(page.getByTestId('rail-jobs-count')).toHaveText('1')
  await expect(page.getByTestId('rail-jobs-zero')).toHaveCount(0)
})

test('docked, the two bottom rows are words: a decorative chevron and Minimize navigation, then Float navigation', async ({ page }) => {
  await docked('docked')({ page })
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/#/library')
  const minimize = page.getByRole('button', { name: 'Minimize navigation' })
  const float = page.getByRole('button', { name: 'Float navigation' })
  await expect(minimize).toHaveText('‹Minimize navigation')
  await expect(minimize.locator('span[aria-hidden="true"]')).toHaveText('‹')
  await expect(float).toHaveText('Float navigation')
  const scale = (await page.evaluate(() => parseFloat(getComputedStyle(document.documentElement).fontSize))) / 15
  for (const button of [minimize, float]) {
    expect(await button.evaluate((el) => parseFloat(getComputedStyle(el).fontSize))).toBeCloseTo(14 * scale, 1)
  }
  await expect(page.locator('.app-rail svg:visible')).toHaveCount(0)
})

test('floating and minimized keep their icons', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.addInitScript(() => localStorage.setItem('baihe.navDock', 'float'))
  await page.goto('/#/drama/2/review')
  const rail = page.locator('.app-rail')
  await expect(rail).not.toHaveClass(/is-listed/)
  await expect(rail.locator('nav a svg:visible').first()).toBeVisible()
  // The mark that shows the open title is still there in the other modes.
  await expect(page.getByTestId('rail-title').locator('.rail-title-mark')).toBeVisible()
  await page.getByRole('button', { name: 'Dock navigation' }).click()
  await page.getByRole('button', { name: 'Minimize navigation' }).click()
  await expect(rail).toHaveClass(/is-minimized/)
  await expect(rail).not.toHaveClass(/is-listed/)
  await expect(rail.locator('nav a svg:visible').first()).toBeVisible()
  await expect(rail.getByTestId('rail-jobs-zero')).toHaveCount(0)
})
