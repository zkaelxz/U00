import { expect, test } from '@playwright/test'

import { guardWrites, mockDiagnostics } from './diagnosticsConsolidateMocks'
import { diagTab, type DiagnosticsTabName } from './diagnosticsTabsNav'
import { enableDeveloperMode } from './sourceHelpers'

// Diagnostics (desktop): its panes listed down the left (the same Tabs component as Settings, with `side`), deep
// links that name a card, attention badges, and Reset library last and set apart.

const heading = (page: import('@playwright/test').Page, title: RegExp) =>
  page.locator('.diag-panel .section-flat-head .section-title, .diag-panel summary .section-title', { hasText: title }).first()

const PANES: DiagnosticsTabName[] = ['Setup', 'Packages and installs', 'Speaker detection', 'Model status', 'Models on disk', 'Loaded now', 'Test models',
  'Resources and ports', 'Log', 'Reset library']

test('the panes are a list down the left; arrows, Home and End move between them; the choice survives a reload', async ({ page }) => {
  const unmocked = await guardWrites(page)
  await mockDiagnostics(page)
  await page.goto('/#/diagnostics')
  const list = page.getByRole('tablist', { name: 'Diagnostics' })
  await expect(list).toHaveAttribute('aria-orientation', 'vertical')
  await expect(list.getByRole('tab')).toHaveText(PANES)
  await expect(diagTab(page, 'Setup')).toHaveAttribute('aria-selected', 'true')
  await diagTab(page, 'Setup').focus()
  await page.keyboard.press('ArrowDown')
  await expect(diagTab(page, 'Packages and installs')).toHaveAttribute('aria-selected', 'true')
  await expect(diagTab(page, 'Packages and installs')).toBeFocused()
  await page.keyboard.press('ArrowRight')
  await expect(diagTab(page, 'Speaker detection')).toHaveAttribute('aria-selected', 'true')
  await page.keyboard.press('End')
  await expect(diagTab(page, 'Reset library')).toHaveAttribute('aria-selected', 'true')
  await page.keyboard.press('ArrowDown')
  await expect(diagTab(page, 'Setup')).toHaveAttribute('aria-selected', 'true')
  await page.keyboard.press('ArrowUp')
  await expect(diagTab(page, 'Reset library')).toHaveAttribute('aria-selected', 'true')
  await diagTab(page, 'Resources and ports').click()
  await expect(heading(page, /^Ports/)).toBeVisible()
  await expect(heading(page, /^Setup/)).toBeHidden()
  await page.reload()
  await expect(diagTab(page, 'Resources and ports')).toHaveAttribute('aria-selected', 'true')
  expect(unmocked).toEqual([])
})

test('a link that names a card lands on its pane, scrolls to the card and focuses its heading', async ({ page }) => {
  await guardWrites(page)
  await mockDiagnostics(page)
  const cases: [string, DiagnosticsTabName, RegExp][] = [
    ['ports', 'Resources and ports', /^Ports/],
    ['log', 'Log', /^Log/],
    ['models', 'Models on disk', /^Models$/],
    ['test-models', 'Test models', /^Test my models/],
    ['speaker', 'Speaker detection', /^Speaker detection/],
    ['packages', 'Packages and installs', /^Packages/],
    ['setup', 'Setup', /^Setup/],
  ]
  for (const [card, pane, title] of cases) {
    await page.goto(`/#/diagnostics?card=${card}`)
    await expect(diagTab(page, pane), card).toHaveAttribute('aria-selected', 'true')
    await expect(heading(page, title), card).toBeInViewport()
    await expect(heading(page, title), card).toBeFocused()
  }
})

test('Reset library is the last item, set apart in red, and its pane holds only the reset', async ({ page }) => {
  await guardWrites(page)
  await mockDiagnostics(page)
  await page.goto('/#/diagnostics')
  const last = page.getByRole('tablist', { name: 'Diagnostics' }).getByRole('tab').last()
  await expect(last).toHaveText('Reset library')
  await expect(last).toHaveClass(/ui-tab-danger/)
  const resources = page.locator('.diag-panel:not([hidden]) .section-title')
  await diagTab(page, 'Resources and ports').click()
  await expect(resources).toHaveText([/^Resources/, /^Ports/])
  await expect(page.getByText('Danger zone')).toHaveCount(0)
  await page.goto('/#/diagnostics?card=reset')
  await expect(page.locator('.diag-panel:not([hidden]) .section-title')).toHaveText(['Reset library'])
})

test('a pane shows its sections in full: nothing to open', async ({ page }) => {
  await guardWrites(page)
  await mockDiagnostics(page)
  await page.goto('/#/diagnostics?tab=model-status')
  await expect(page.locator('.diag-panel:not([hidden]) details.section')).toHaveCount(0)
  await expect(heading(page, /^Model status/)).toBeVisible()
  await diagTab(page, 'Packages and installs').click()
  await expect(heading(page, /^Packages/)).toBeVisible()
})

test('Models on disk has the Benchmark Lab link and Test models its own pane; the old places are gone', async ({ page }) => {
  await guardWrites(page)
  await mockDiagnostics(page)
  await enableDeveloperMode(page)
  await page.goto('/#/diagnostics?tab=models')
  await expect(page.getByRole('link', { name: 'Open Benchmark Lab' })).toHaveAttribute('href', '#/benchmark')
  await expect(page.getByRole('button', { name: 'Show model engines' })).toHaveCount(0)
  await expect(page.getByTestId('engines-moved')).toHaveCount(0)
  await expect(page.getByText('Test engines and prompts against golden sets.')).toHaveCount(0)
  await expect(page.getByText('Real-model check')).toHaveCount(0)
  await diagTab(page, 'Test models').click()
  await expect(heading(page, /^Test my models/)).toBeVisible()
})

test('panes show a badge for what needs attention', async ({ page }) => {
  await guardWrites(page)
  await mockDiagnostics(page, { ffmpegFound: false })
  await page.goto('/#/diagnostics')
  await expect(diagTab(page, 'Setup')).toContainText('1 to look at')
  await expect(diagTab(page, 'Resources and ports')).not.toContainText('to look at')
})

test('a link to a card works again after the pane was changed by hand', async ({ page }) => {
  await guardWrites(page)
  await mockDiagnostics(page)
  await page.goto('/#/diagnostics?card=ports')
  await expect(diagTab(page, 'Resources and ports')).toHaveAttribute('aria-selected', 'true')
  // Moving to another pane by hand and back by a link to the same address still works.
  await diagTab(page, 'Setup').click()
  await page.goto('/#/diagnostics?card=models')
  await expect(diagTab(page, 'Models on disk')).toHaveAttribute('aria-selected', 'true')
})
