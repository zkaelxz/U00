import { expect, test, type Page } from '@playwright/test'

import { mockDomains } from './sourceDomainsMocks'
import { mockSources, openManage } from './sourcesMocks'

// Manage sources (#/sources/manage), desktop 1280x800: one level of tabs, the fold rule,
// the empty state, and no sideways scroll in light and dark. Every call is mocked.

test.use({ viewport: { width: 1280, height: 800 } })

const TABS = ['Sites', 'Timing & cache', 'Network', 'Activity']

async function noSideways(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

test('every tab fits at 1280x800 in light and dark, with one level of tabs', async ({ page }) => {
  const s = await mockSources(page)
  await mockDomains(page)
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme })
    const manage = await openManage(page)
    await expect(manage.getByRole('heading', { level: 2, name: 'Manage sources' })).toBeVisible()
    for (const name of TABS) {
      await manage.getByRole('tab', { name }).click()
      await expect(manage.getByRole('tab', { name })).toHaveAttribute('aria-selected', 'true')
      await noSideways(page)
      await page.screenshot({ path: `test-results/screens/sources-manage-${name.split(' ')[0].toLowerCase()}-${scheme}.png`, fullPage: true })
    }
    // One tablist, and no tab or tablist nested in a panel.
    await expect(page.getByRole('tablist')).toHaveCount(1)
    await expect(manage.getByRole('tabpanel').getByRole('tab')).toHaveCount(0)
  }
  expect(s.unmocked).toEqual([])
})

test('the header matches the other tabbed pages: breadcrumb, title row, back link', async ({ page }) => {
  await mockSources(page)
  const manage = await openManage(page)
  await expect(page.getByRole('navigation', { name: 'Breadcrumb' }).getByRole('link', { name: 'Sources' })).toBeVisible()
  const head = manage.locator('header.page-head')
  await expect(head.getByRole('heading', { level: 2 })).toHaveText('Manage sources')
  await head.getByRole('link', { name: 'Back to Sources' }).click()
  await expect(page).toHaveURL(/#\/sources$/)
  // The Sources page leads here with one quiet link.
  await page.getByRole('link', { name: 'Manage sources' }).click()
  await expect(page).toHaveURL(/#\/sources\/manage$/)
})

test('Sites is a compact row: name, status, On, Rules, Details', async ({ page }) => {
  await mockSources(page)
  const manage = await openManage(page)
  await expect(manage.getByRole('columnheader')).toHaveText(['Site', 'Status', 'On', 'Rules', 'Details'])
  const row = manage.locator('tbody tr').first()
  await expect(row.getByRole('switch', { name: 'On: Alpha Comics' })).toBeVisible()
  await expect(row.getByRole('combobox', { name: 'Rules: Alpha Comics' })).toBeVisible()
  await expect(row.getByRole('button', { name: 'Details: Alpha Comics' })).toBeVisible()
  // Nothing else sits in the row: no Adult cell, no Sign-in column.
  await expect(manage.getByRole('columnheader', { name: /Adult|Sign-in|Health/ })).toHaveCount(0)
})

test('a tab link opens that tab, and keyboard focus is visible on its controls', async ({ page }) => {
  await mockSources(page)
  await mockDomains(page)
  const manage = await openManage(page, 'network')
  await expect(manage.getByRole('tab', { name: 'Network' })).toHaveAttribute('aria-selected', 'true')
  await manage.getByRole('tab', { name: 'Sites' }).click()
  const details = manage.getByRole('button', { name: 'Details: Alpha Comics' })
  for (let i = 0; i < 40 && !(await details.evaluate((e) => e === document.activeElement)); i++) await page.keyboard.press('Tab')
  await expect(details).toBeFocused()
  const ring = await details.evaluate((e) => {
    const c = getComputedStyle(e)
    return c.outlineStyle !== 'none' && parseFloat(c.outlineWidth) > 0 ? 'outline' : c.boxShadow !== 'none' ? 'shadow' : 'none'
  })
  expect(ring, 'focus ring').not.toBe('none')
})

test('fold rule: the first card is open the first time, the rest closed, and the choice is remembered', async ({ page }) => {
  await mockSources(page)
  const manage = await openManage(page, 'timing')
  const card = (title: string) => page.locator(`details.section:has(> summary > .section-title:text-is("${title}"))`)
  await expect(card('Pacing')).toHaveAttribute('open', '')
  await expect(card('Saved pages')).not.toHaveAttribute('open', '')
  await expect(card('Advanced timing')).not.toHaveAttribute('open', '')
  await card('Saved pages').locator(':scope > summary').click()
  await card('Pacing').locator(':scope > summary').click()
  await page.reload()
  await expect(manage.getByRole('tab', { name: 'Timing & cache' })).toHaveAttribute('aria-selected', 'true')
  await expect(card('Saved pages')).toHaveAttribute('open', '')
  await expect(card('Pacing')).not.toHaveAttribute('open', '')
})

test('Activity with nothing to list says so instead of showing a blank tab', async ({ page }) => {
  const s = await mockSources(page)
  const manage = await openManage(page, 'activity')
  await expect(manage.getByText('Nothing yet. Pages you import from a link are listed here.')).toBeVisible()
  await expect(manage.getByRole('tabpanel').locator('details.section')).toHaveCount(0)
  expect(s.unmocked).toEqual([])
})
