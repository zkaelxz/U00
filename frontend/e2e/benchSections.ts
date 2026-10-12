import { expect, type Page } from '@playwright/test'

// The four Benchmark Lab sections are tabs; specs that work inside a card open its tab first.
// The section is the first (only) card of its tab, so it is open once the tab is.
export async function openBenchTab(page: Page, title: 'Golden sets' | 'Run a benchmark' | 'Model re-evaluation' | 'Recent runs'): Promise<void> {
  await page.getByRole('tablist', { name: 'Benchmark Lab' }).getByRole('tab', { name: title, exact: true }).click()
  await expect(page.getByRole('region', { name: title }).locator('.bench-section-body')).toBeVisible()
}
