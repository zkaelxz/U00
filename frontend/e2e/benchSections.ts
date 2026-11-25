import { expect, type Page } from '@playwright/test'

import { pickTab } from './tabPicker'

// The Benchmark Lab sections are tabs (a picker on a phone); specs that work inside a card open its tab first.
// The section is the first (only) card of its tab, so it is open once the tab is.
export async function openBenchTab(page: Page, title: 'Golden sets' | 'Run a benchmark' | 'Model re-evaluation' | 'Recent runs'): Promise<void> {
  await pickTab(page, title, 'Benchmark Lab')
  await expect(page.getByRole('region', { name: title }).locator('.bench-section-body')).toBeVisible()
}
