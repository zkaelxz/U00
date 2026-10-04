import { expect, test, type Page } from '@playwright/test'

import { mockPhoneTables } from './phoneTablesMocks'

// Desktop: the Characters and Glossary tables keep their real table layout (the card layout is phone-only).

test.use({ viewport: { width: 1280, height: 900 } })

test.beforeEach(async ({ page }) => {
  await mockPhoneTables(page)
})
test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

async function open(page: Page, title: string) {
  const summary = page.locator('summary').filter({ has: page.locator('.section-title', { hasText: new RegExp(`^${title}$`) }) }).first()
  if ((await summary.locator('xpath=..').getAttribute('open')) === null) await summary.click()
}

test('characters and glossary stay tables at 1280', async ({ page }) => {
  await page.goto('/#/drama/1/translate')
  await page.waitForLoadState('networkidle')
  await open(page, 'Characters')
  await open(page, 'Glossary')
  for (const name of ['Characters', 'Glossary']) {
    const table = page.getByRole('region', { name }).locator('table')
    await expect(table).toHaveCSS('display', 'table')
    await expect(table.locator('thead')).toHaveCSS('display', 'table-header-group')
    await expect(table.locator('tbody tr').first()).toHaveCSS('display', 'table-row')
    await expect(table.locator('td').first()).toHaveCSS('display', 'table-cell')
    expect(await table.evaluate((t) => t.scrollWidth <= t.parentElement!.clientWidth)).toBe(true)
  }
  const chars = page.getByRole('region', { name: 'Characters' })
  await expect(chars.getByRole('columnheader', { name: 'Speaker' })).toBeVisible()
  await expect(chars.getByRole('columnheader', { name: 'Reference' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Glossary' }).getByRole('columnheader', { name: 'Aliases' })).toBeVisible()
  // The label pseudo-elements are phone-only.
  const before = await chars.locator('td[data-label="Lines"]').first().evaluate((e) => getComputedStyle(e, '::before').content)
  expect(before).toBe('none')
})
