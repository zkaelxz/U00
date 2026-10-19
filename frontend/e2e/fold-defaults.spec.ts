import { expect, test, type Page } from '@playwright/test'

import { guardWrites, mockDiagnostics } from './diagnosticsConsolidateMocks'
import { diagTab } from './diagnosticsTabsNav'
import { openFold } from './foldHelpers'

// Every tabbed page: the first visit to a tab opens its first card only, and after that the person's
// own folds stay as left, even when an earlier version had saved every card closed.

// Top-level cards of the visible tab (a Section inside a card does not count).
const topLevel = (page: Page, panel: string) => page.locator(`${panel}:not([hidden]) details.section:not(details.section details.section)`)
const openTitles = (page: Page, panel: string) =>
  topLevel(page, panel).evaluateAll((els) =>
    els.filter((e) => (e as HTMLDetailsElement).open).map((e) => e.querySelector('summary .section-title')?.textContent ?? ''))
// The fold choice is saved from the details 'toggle' event, which fires after the click returns; a reload before it lands loses it.
const waitForStored = (page: Page, keyPart: string, value: '0' | '1') =>
  page.waitForFunction(([k, v]) => Object.entries(window.localStorage).some(([key, val]) => key.includes(k) && val === v), [keyPart, value] as const)

test('Settings: one card open per tab on a fresh browser, and a fold made by hand stays', async ({ page }) => {
  await page.goto('/#/settings')
  const tabs = page.getByRole('tab')
  await expect(tabs).toHaveCount(6)
  for (const name of ['Translation and keys', 'Costs', 'Notifications and backups', 'Account and display', 'Integrations', 'Performance and advanced']) {
    await page.getByRole('tab', { name, exact: true }).click()
    await page.locator('.settings-panel:not([hidden])').waitFor()
    expect(await openTitles(page, '.settings-panel')).toHaveLength(1)
  }
  await page.getByRole('tab', { name: 'Translation and keys', exact: true }).click()
  const first = topLevel(page, '.settings-panel').first()
  await first.locator('> summary').click()
  await expect(first).not.toHaveAttribute('open', '')
  await waitForStored(page, 'settings.card.engine-routing', '0')
  await page.reload()
  await page.getByRole('tab', { name: 'Translation and keys', exact: true }).click()
  expect(await openTitles(page, '.settings-panel')).toHaveLength(0)
})

test('Settings: an all-collapsed state saved by an earlier version does not become the default', async ({ page }) => {
  await page.addInitScript(() => {
    for (const id of ['engine-routing', 'defaults', 'language-packs']) window.localStorage.setItem(`baihe.section.settings.card.${id}`, '0')
  })
  await page.goto('/#/settings')
  await page.getByRole('tab', { name: 'Translation and keys', exact: true }).click()
  await page.locator('.settings-panel:not([hidden])').waitFor()
  expect(await openTitles(page, '.settings-panel')).toHaveLength(1)
})

test('Diagnostics: one card open per tab on a fresh browser, and a fold made by hand stays', async ({ page }) => {
  await guardWrites(page)
  await mockDiagnostics(page)
  await page.goto('/#/diagnostics')
  for (const name of ['Setup', 'Packages and installs', 'Models', 'System'] as const) {
    await diagTab(page, name).click()
    await expect(diagTab(page, name)).toHaveAttribute('aria-selected', 'true')
    await expect.poll(async () => (await openTitles(page, '.diag-panel')).length).toBe(1)
  }
  await diagTab(page, 'System').click()
  const ports = topLevel(page, '.diag-panel').first()
  await ports.locator('> summary').click()
  await waitForStored(page, 'diagnostics.ports', '0')
  await page.reload()
  await diagTab(page, 'System').click()
  expect(await openTitles(page, '.diag-panel')).toHaveLength(0)
})

const EMPTY = { items: [] }
async function mockEmptyLibrary(page: Page) {
  for (const path of ['series', 'presets', 'voice-bank', 'costs', 'history'])
    await page.route(`**/api/library/${path}`, (r) => r.fulfill({ json: EMPTY }))
  await page.route('**/api/saved-comics/series', (r) => r.fulfill({ json: [] }))
}

test.describe('Library tools', () => {
  test('Organize and Activity say so when every section is empty, instead of showing nothing', async ({ page }) => {
    await mockEmptyLibrary(page)
    await page.goto('/#/library-tools?tab=organize')
    await expect(page.locator('.tools-panel:not([hidden]) [data-testid=tools-empty]')).toContainText('No shared series, profiles, voice-bank entries or saved manga yet')
    await expect(page.getByRole('heading', { name: 'Export' })).toBeVisible()
    await page.getByRole('tab', { name: 'Activity', exact: true }).click()
    await expect(page.locator('.tools-panel:not([hidden]) [data-testid=tools-empty]')).toContainText('No translation costs or reading history yet')
  })

  test('an empty section stays hidden while another has content, and that one starts open', async ({ page }) => {
    await mockEmptyLibrary(page)
    await page.route('**/api/library/presets', (r) => r.fulfill({ json: { items: [{ id: 7, name: 'My preset', translation_engine: null }] } }))
    await page.goto('/#/library-tools?tab=organize')
    // Loaded first, so the empty notice can't pass by not having rendered yet; then only the
    // open tab: the hidden Activity tab rightly says it is empty once its own lists load.
    await expect.poll(() => openTitles(page, '.tools-panel')).toEqual(['Profiles'])
    await expect(page.locator('.tools-panel:not([hidden]) [data-testid=tools-empty]')).toHaveCount(0)
  })

  test('Backup runs Back up, Restore, Automatic backups, Saved copies, with one card open and no Export', async ({ page }) => {
    await page.goto('/#/library-tools?tab=backup')
    await expect(topLevel(page, '.tools-panel').locator('> summary .section-title')).toHaveText(['Back up', 'Restore', 'Automatic backups', 'Saved automatic copies'])
    expect(await openTitles(page, '.tools-panel')).toEqual(['Back up'])
    await expect(page.getByRole('button', { name: /Export all translated/ })).toBeHidden()
  })

  test('every Library tools tab starts with one card open', async ({ page }) => {
    await page.goto('/#/library-tools?tab=storage')
    await expect.poll(() => openTitles(page, '.tools-panel')).toEqual(['Cleanup'])
    await page.getByRole('tab', { name: 'Data folder', exact: true }).click()
    await expect.poll(() => openTitles(page, '.tools-panel')).toEqual(['Folders and files'])
  })
})
