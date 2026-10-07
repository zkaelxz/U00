import { expect, type Locator, type Page } from '@playwright/test'

export const SECTIONS = ['Golden sets', 'Run a benchmark', 'Model re-evaluation', 'Recent runs'] as const
export const DEFAULT_OPEN: Record<string, boolean> = {
  'Golden sets': true,
  'Run a benchmark': false,
  'Model re-evaluation': false,
  'Recent runs': false,
}

export const toggle = (page: Page, title: string): Locator => page.getByRole('button', { name: title, exact: true })

export async function expectExpanded(page: Page, want: Record<string, boolean>): Promise<void> {
  for (const [title, open] of Object.entries(want)) {
    await expect(toggle(page, title)).toHaveAttribute('aria-expanded', String(open))
  }
}

export async function openBench(page: Page): Promise<void> {
  await page.goto('/#/benchmark')
  await expect(page.locator('.bench-section-toggle')).toHaveCount(4)
}

// The (i) next to a heading shows the numbered steps on click and hides them with Escape.
export async function checkHelp(page: Page, title: string, firstStep: RegExp): Promise<void> {
  const help = page.getByRole('button', { name: `Help: ${title}` })
  await help.click()
  await expect(page.getByRole('tooltip').filter({ hasText: firstStep })).toBeVisible()
  await help.press('Escape')
  await expect(page.getByRole('tooltip').filter({ hasText: firstStep })).toBeHidden()
}
