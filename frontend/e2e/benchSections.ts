import { expect, type Page } from '@playwright/test'

import { pickTab } from './tabPicker'

export type BenchPane = 'Golden sets' | 'Add cases' | 'Run a benchmark' | 'Settings sweep' | 'Translation sweep' | 'Model re-evaluation' | 'Recent runs'

// The Benchmark Lab sections are listed down the left (one scrolling row on a phone); specs that work inside one open it first.
export async function openBenchTab(page: Page, title: BenchPane): Promise<void> {
  await pickTab(page, title, 'Benchmark Lab')
  await expect(page.getByRole('tab', { name: title, exact: true })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('tabpanel')).toBeVisible()
}

// A flat section inside a pane, found by its heading.
export const flatSection = (page: Page, heading: string) =>
  page.locator('.section-flat').filter({ has: page.getByRole('heading', { name: heading, exact: true }) })
