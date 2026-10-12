import { expect, test } from '@playwright/test'

import { guardWrites, mockDiagnostics } from './diagnosticsConsolidateMocks'
import { diagTab } from './diagnosticsTabsNav'

// Diagnostics (desktop): four tabs built on the same Tabs component as Settings, deep links that name
// a card, attention badges, and which cards start open.

const fold = (page: import('@playwright/test').Page, title: RegExp) =>
  page.locator('details.section', { has: page.locator('> summary', { hasText: title }) }).first()

test('four tabs; arrows, Home and End move between them; the choice survives a reload', async ({ page }) => {
  const unmocked = await guardWrites(page)
  await mockDiagnostics(page)
  await page.goto('/#/diagnostics')
  const tabs = page.getByRole('tablist', { name: 'Diagnostics' }).getByRole('tab')
  await expect(tabs).toHaveText(['Setup', 'Packages and installs', 'Models', 'System'])
  await expect(diagTab(page, 'Setup')).toHaveAttribute('aria-selected', 'true')
  await diagTab(page, 'Setup').focus()
  await page.keyboard.press('ArrowRight')
  await expect(diagTab(page, 'Packages and installs')).toHaveAttribute('aria-selected', 'true')
  await expect(diagTab(page, 'Packages and installs')).toBeFocused()
  await page.keyboard.press('End')
  await expect(diagTab(page, 'System')).toHaveAttribute('aria-selected', 'true')
  await page.keyboard.press('ArrowRight')
  await expect(diagTab(page, 'Setup')).toHaveAttribute('aria-selected', 'true')
  await page.keyboard.press('ArrowLeft')
  await expect(diagTab(page, 'System')).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('summary', { hasText: /^Ports/ })).toBeVisible()
  await expect(page.locator('summary', { hasText: /^Setup/ })).toBeHidden()
  await page.reload()
  await expect(diagTab(page, 'System')).toHaveAttribute('aria-selected', 'true')
  expect(unmocked).toEqual([])
})

test('a link that names a card lands on its tab, scrolls to the card and opens it', async ({ page }) => {
  await guardWrites(page)
  await mockDiagnostics(page)
  const cases: [string, 'Setup' | 'Packages and installs' | 'Models' | 'System', RegExp][] = [
    ['ports', 'System', /^Ports/],
    ['log', 'System', /^Log/],
    ['models', 'Models', /^Models/],
    ['test-models', 'Models', /^Test my models/],
    ['speaker', 'Packages and installs', /^Speaker detection/],
    ['packages', 'Packages and installs', /^Packages/],
    ['setup', 'Setup', /^Setup/],
  ]
  for (const [card, tab, title] of cases) {
    await page.goto(`/#/diagnostics?card=${card}`)
    await expect(diagTab(page, tab), card).toHaveAttribute('aria-selected', 'true')
    await expect(fold(page, title), card).toHaveJSProperty('open', true)
    await expect(fold(page, title), card).toBeInViewport()
  }
})

test('Reset library is the last card of System and stays closed unless a link names it', async ({ page }) => {
  await guardWrites(page)
  await mockDiagnostics(page)
  await page.goto('/#/diagnostics?tab=system')
  const summaries = page.locator('.diag-panel:not([hidden]) details.section > summary')
  await expect(summaries.first()).toContainText('Ports')
  await expect(summaries.last()).toContainText('Reset library')
  await expect(fold(page, /^Reset library/)).toHaveJSProperty('open', false)
  await expect(page.getByText('Danger zone')).toHaveCount(0)
  await page.goto('/#/diagnostics?card=reset')
  await expect(fold(page, /^Reset library/)).toHaveJSProperty('open', true)
})

test('short tabs start open and each card collapses and is remembered; Packages starts with only the used cards open', async ({ page }) => {
  await guardWrites(page)
  await mockDiagnostics(page)
  await page.goto('/#/diagnostics?tab=models')
  for (const title of [/^Model status/, /^Models/, /^Test my models/]) await expect(fold(page, title)).toHaveJSProperty('open', true)
  await fold(page, /^Models/).locator('> summary').click()
  await expect(fold(page, /^Models/)).toHaveJSProperty('open', false)
  await expect(fold(page, /^Models/).locator('> summary')).toContainText('GB')
  // The fold choice is saved from the details 'toggle' event, which fires after the click returns; reloading first loses it.
  await page.waitForFunction(() => localStorage.getItem('baihe.section.diagnostics.models') === '0')
  await page.reload()
  await expect(fold(page, /^Models/)).toHaveJSProperty('open', false)

  await page.goto('/#/diagnostics?tab=packages')
  await expect(fold(page, /^Packages/)).toHaveJSProperty('open', true)
  await expect(fold(page, /^Speaker detection/)).toHaveJSProperty('open', false)
})

test('the Models tab has the Benchmark Lab link and Test my models; the old places are gone', async ({ page }) => {
  await guardWrites(page)
  await mockDiagnostics(page)
  await page.goto('/#/diagnostics?tab=models')
  await expect(page.getByRole('link', { name: 'Open Benchmark Lab' })).toHaveAttribute('href', '#/benchmark')
  await expect(page.getByRole('button', { name: 'Show model engines' })).toHaveCount(0)
  await expect(page.getByTestId('engines-moved')).toHaveCount(0)
  await expect(page.getByText('Test engines and prompts against golden sets.')).toHaveCount(0)
  await expect(page.getByText('Real-model check')).toHaveCount(0)
})

test('tabs show a badge for what needs attention', async ({ page }) => {
  await guardWrites(page)
  await mockDiagnostics(page, { ffmpegFound: false })
  await page.goto('/#/diagnostics')
  await expect(diagTab(page, 'Setup')).toContainText('1 to look at')
  await expect(diagTab(page, 'System')).not.toContainText('to look at')
})

test('a link to a card works again after the tab was changed by hand', async ({ page }) => {
  await guardWrites(page)
  await mockDiagnostics(page)
  await page.goto('/#/diagnostics?card=ports')
  await expect(diagTab(page, 'System')).toHaveAttribute('aria-selected', 'true')
  // Moving to another tab by hand and back by a link to the same address still works.
  await diagTab(page, 'Setup').click()
  await page.goto('/#/diagnostics?card=models')
  await expect(diagTab(page, 'Models')).toHaveAttribute('aria-selected', 'true')
})
