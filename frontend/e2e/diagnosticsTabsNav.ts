import { expect, type Page } from '@playwright/test'

export type DiagnosticsTabName =
  | 'Setup' | 'Packages and installs' | 'Speaker detection' | 'Model status' | 'Models on disk' | 'Loaded now' | 'Test models'
  | 'Resources and ports' | 'Log' | 'Reset library'

export const diagTab = (page: Page, name: DiagnosticsTabName) =>
  page.getByRole('tab', { name: new RegExp(`^${name}`) })

/** Selects a Diagnostics pane in the list down the left; only that pane's sections are visible. */
export async function openDiagTab(page: Page, name: DiagnosticsTabName) {
  await diagTab(page, name).click()
  await expect(diagTab(page, name)).toHaveAttribute('aria-selected', 'true')
}

// The pane that holds each top-level Diagnostics section, by the start of its title. The first match wins, so a
// title that starts with another ("Models in memory", "Models") comes before it.
const CARD_TABS: [string, DiagnosticsTabName][] = [
  ['Setup', 'Setup'],
  ['Recommended installs', 'Setup'],
  ['Browser support', 'Setup'],
  ['ffmpeg', 'Setup'],
  ['CUDA runtime', 'Setup'],
  ['JavaScript runtime', 'Setup'],
  ['Packages', 'Packages and installs'],
  ['Voice cloning', 'Packages and installs'],
  ['Speaker detection', 'Speaker detection'],
  ['Model status', 'Model status'],
  ['Models in memory', 'Loaded now'],
  ['Memory by process', 'Loaded now'],
  ['Models', 'Models on disk'],
  ['Test my models', 'Test models'],
  ['Resources', 'Resources and ports'],
  ['Ports', 'Resources and ports'],
  ['Log', 'Log'],
  ['Reset library', 'Reset library'],
]

/** Opens the tab that holds the card whose summary text matches `title`; other titles (folds inside a card) are left alone. */
export async function openTabFor(page: Page, title: RegExp) {
  const start = title.source.replace(/^\^/, '')
  const hit = CARD_TABS.find(([name]) => start.startsWith(name))
  if (hit) await openDiagTab(page, hit[1])
}

/** A section of the pane on show, by the start of its heading; panes draw their sections flat, with no fold to open. */
export const diagSection = (page: Page, title: RegExp) =>
  page.locator('.diag-panel:not([hidden]) div.section-flat', { has: page.locator('> .section-flat-head > .section-title', { hasText: title }) }).first()

/** The heading row of that section: title, count and its one-line state. */
export const diagSectionHead = (page: Page, title: RegExp) => diagSection(page, title).locator('> .section-flat-head')
