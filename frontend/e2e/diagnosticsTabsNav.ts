import { expect, type Page } from '@playwright/test'

export type DiagnosticsTabName = 'Setup' | 'Packages and installs' | 'Models' | 'System'

export const diagTab = (page: Page, name: DiagnosticsTabName) =>
  page.getByRole('tab', { name: new RegExp(`^${name}`) })

/** Selects a Diagnostics tab; only the selected tab's folds are visible. */
export async function openDiagTab(page: Page, name: DiagnosticsTabName) {
  await diagTab(page, name).click()
  await expect(diagTab(page, name)).toHaveAttribute('aria-selected', 'true')
}

// The tab that holds each top-level Diagnostics card, by the start of its title.
const CARD_TABS: [string, DiagnosticsTabName][] = [
  ['Setup', 'Setup'],
  ['Packages', 'Packages and installs'],
  ['Speaker detection', 'Packages and installs'],
  ['Browser support', 'Packages and installs'],
  ['JavaScript runtime', 'Packages and installs'],
  ['Model status', 'Models'],
  ['Models', 'Models'],
  ['Test my models', 'Models'],
  ['Ports', 'System'],
  ['Log', 'System'],
  ['Reset library', 'System'],
]

/** Opens the tab that holds the card whose summary text matches `title`; other titles (folds inside a card) are left alone. */
export async function openTabFor(page: Page, title: RegExp) {
  const start = title.source.replace(/^\^/, '')
  const hit = CARD_TABS.find(([name]) => start.startsWith(name))
  if (hit) await openDiagTab(page, hit[1])
}
