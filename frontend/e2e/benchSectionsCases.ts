import { expect, type Locator, type Page } from '@playwright/test'

import { expectPickedTab, pickTab } from './tabPicker'

export const SECTIONS = [
  'Quality', 'Golden sets', 'Add cases', 'Run a benchmark', 'Settings sweep', 'Translation sweep', 'Model re-evaluation', 'Recent runs',
] as const

export const benchTab = (page: Page, title: string): Locator =>
  page.getByRole('tablist', { name: 'Benchmark Lab' }).getByRole('tab', { name: title, exact: true })

export async function selectTab(page: Page, title: string): Promise<void> {
  await pickTab(page, title, 'Benchmark Lab')
  await expectPickedTab(page, 'Benchmark Lab', title)
}

export async function openBench(page: Page): Promise<void> {
  await page.goto('/#/benchmark')
  // Every section is mounted from the start, its pane hidden until picked.
  await expect(page.locator('section.bench-section')).toHaveCount(6)
}

// The (i) next to a heading shows the numbered steps on click and hides them with Escape.
export async function checkHelp(page: Page, title: string, firstStep: RegExp): Promise<void> {
  await selectTab(page, title)
  const help = page.getByRole('button', { name: `Help: ${title}` })
  await help.click()
  await expect(page.getByRole('tooltip').filter({ hasText: firstStep })).toBeVisible()
  await help.press('Escape')
  await expect(page.getByRole('tooltip').filter({ hasText: firstStep })).toBeHidden()
}
