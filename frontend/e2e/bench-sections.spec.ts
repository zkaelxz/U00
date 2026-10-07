import { expect, test } from '@playwright/test'

import { DEFAULT_OPEN, checkHelp, expectExpanded, openBench, toggle } from './benchSectionsCases'

// Benchmark Lab page: the four sections fold under their headings, the choice survives a reload, and
// the intro and the (i) help explain the steps. Read-only against the seeded e2e library.

test('intro, defaults and folding by keyboard', async ({ page }) => {
  await openBench(page)
  await expect(page.getByTestId('bench-intro')).toContainText('Your titles and lines are never touched')
  await expectExpanded(page, DEFAULT_OPEN)

  const runs = toggle(page, 'Recent runs')
  await runs.focus()
  await page.keyboard.press('Enter')
  await expect(runs).toHaveAttribute('aria-expanded', 'true')
  await expect(page.getByRole('region', { name: 'Recent runs' }).locator('.bench-section-body')).toBeVisible()
  await page.keyboard.press('Space')
  await expect(runs).toHaveAttribute('aria-expanded', 'false')
  await expect(page.getByRole('region', { name: 'Recent runs' }).locator('.bench-section-body')).toBeHidden()
})

test('the open and closed choice survives a reload', async ({ page }) => {
  await openBench(page)
  await toggle(page, 'Golden sets').click()
  await toggle(page, 'Model re-evaluation').click()
  await page.reload()
  await expect(page.locator('.bench-section-toggle')).toHaveCount(4)
  await expectExpanded(page, { ...DEFAULT_OPEN, 'Golden sets': false, 'Model re-evaluation': true })
})

test('each heading has a purpose line and a help that lists the steps', async ({ page }) => {
  await openBench(page)
  await expect(page.getByRole('region', { name: 'Golden sets' })).toContainText('The reference translations that runs are scored against.')
  await checkHelp(page, 'Golden sets', /1\. Import a set/)
  await checkHelp(page, 'Run a benchmark', /3\. Press Estimate cost/)
  await checkHelp(page, 'Model re-evaluation', /1\. Production is the model you use now/)
  await checkHelp(page, 'Recent runs', /2\. Results shows one run/)
})
