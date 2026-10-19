import { expect, test } from './fixtures'
import { clearLines, mockSnapshot, seedLines } from './snapshotPreviewMocks'
import { enableDeveloperMode } from './sourceHelpers'

// The page frame: one main landmark, one h1 that names the page, a skip link and a title that follows the page.

const PAGES: { hash: string; h1: string }[] = [
  { hash: '#/', h1: 'Library' },
  { hash: '#/settings', h1: 'Settings' },
  { hash: '#/jobs', h1: 'Jobs' },
  { hash: '#/translate', h1: 'Quick translate' },
  { hash: '#/make-subtitles', h1: 'Make subtitles' },
  { hash: '#/diagnostics', h1: 'Diagnostics' },
  { hash: '#/benchmark', h1: 'Benchmark Lab' },
  { hash: '#/sources', h1: 'Sources' },
  { hash: '#/manga', h1: 'Saved manga' },
  { hash: '#/library-tools', h1: 'Library tools' },
  { hash: '#/drama/3', h1: 'Signal' },
]

for (const width of [360, 768, 1440]) {
  for (const { hash, h1 } of PAGES) {
    test(`one main, one h1 and a matching title: ${hash} at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 })
      // The Benchmark Lab page is behind Developer Mode.
      await enableDeveloperMode(page)
      await page.goto(`/${hash}`)
      const heading = page.getByRole('heading', { level: 1 })
      await expect(heading).toHaveCount(1)
      await expect(heading).toContainText(h1)
      await expect(page.locator('main, [role="main"]')).toHaveCount(1)
      await expect(page.locator('.app-brand, .rail-brand').first()).toBeVisible()
      await expect(page).toHaveTitle(`${h1} · Baihe Studio`)
    })
  }
}

test('the title follows the page when the route changes', async ({ page }) => {
  await page.goto('/#/settings')
  await expect(page).toHaveTitle('Settings · Baihe Studio')
  await page.goto('/#/jobs')
  await expect(page).toHaveTitle('Jobs · Baihe Studio')
})

test('the first Tab stop is a skip link that moves focus into main', async ({ page }) => {
  await page.goto('/#/settings')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Settings')
  await page.keyboard.press('Tab')
  const skip = page.getByRole('link', { name: 'Skip to content' })
  await expect(skip).toBeFocused()
  await expect(skip).toBeVisible()
  await page.keyboard.press('Enter')
  await expect(page.locator('main')).toBeFocused()
  expect(new URL(page.url()).hash).toBe('#/settings')
})

test.describe('Line history', () => {
  test.beforeEach(() => seedLines())
  test.afterAll(() => clearLines())

  test('Preview and Restore say which version they act on', async ({ page }) => {
    await mockSnapshot(page)
    const list = page.getByTestId('history-list')
    await expect(list.getByRole('button', { name: /^Preview version: before merge \(\w{3} \d{1,2}, 2026, [^)]+\)$/ })).toBeVisible()
    await expect(list.getByRole('button', { name: /^Restore version: before merge \(\w{3} \d{1,2}, 2026, [^)]+\)$/ })).toBeVisible()
  })
})
