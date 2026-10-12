import { expect, test, type Page } from '@playwright/test'

import { mockDomains } from './sourceDomainsMocks'
import { mockSources, openManage } from './sourcesMocks'

// Sources tabs and Manage sources, desktop 1280x800: one level of tabs, the fold rule, the old
// #/sources/manage link, the empty state, and no sideways scroll in light and dark. Every call is mocked.

test.use({ viewport: { width: 1280, height: 800 } })

const TABS = ['Find', 'New chapters', 'Manage sources']

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
    await openManage(page)
    await expect(page.getByRole('heading', { level: 2, name: 'Sources' })).toBeVisible()
    for (const name of TABS) {
      await page.getByRole('tab', { name }).click()
      await expect(page.getByRole('tab', { name })).toHaveAttribute('aria-selected', 'true')
      await noSideways(page)
      await page.screenshot({ path: `test-results/screens/sources-${name.split(' ')[0].toLowerCase()}-${scheme}.png`, fullPage: true })
    }
    // One tablist, and no tab or tablist nested in a panel.
    await expect(page.getByRole('tablist')).toHaveCount(1)
    await expect(page.getByRole('tabpanel').getByRole('tab')).toHaveCount(0)
  }
  expect(s.unmocked).toEqual([])
})

test('the header is the Sources page header with its tabs; switching tabs keeps the Find search', async ({ page }) => {
  await mockSources(page)
  await page.goto('/#/sources')
  await expect(page.getByRole('navigation', { name: 'Breadcrumb' })).toHaveCount(0)
  await expect(page.locator('header.page-head').getByRole('heading', { level: 2 })).toHaveText('Sources')
  await page.getByRole('searchbox', { name: 'Title' }).fill('Heaven')
  await page.getByRole('tab', { name: 'Manage sources' }).click()
  await expect(page).toHaveURL(/#\/sources\?tab=manage$/)
  await page.goBack()
  await expect(page.getByRole('tab', { name: 'Find' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('searchbox', { name: 'Title' })).toHaveValue('Heaven')
  // Find leads here with one quiet line.
  await page.getByRole('tabpanel').getByRole('link', { name: 'Manage sources' }).click()
  await expect(page).toHaveURL(/#\/sources\?tab=manage$/)
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

test('the old Manage sources link opens that tab and scrolls to the area; keyboard focus is visible on its controls', async ({ page }) => {
  await mockSources(page)
  await mockDomains(page)
  const manage = await openManage(page, 'network')
  await expect(page.getByRole('tab', { name: 'Manage sources' })).toHaveAttribute('aria-selected', 'true')
  await expect(manage.locator('#sources-manage-network')).toBeInViewport()
  await page.getByRole('tab', { name: 'Find' }).click()
  await expect(page).toHaveURL(/#\/sources$/)
  await page.getByRole('tab', { name: 'Manage sources' }).click()
  const details = manage.getByRole('button', { name: 'Details: Alpha Comics' })
  for (let i = 0; i < 60 && !(await details.evaluate((e) => e === document.activeElement)); i++) await page.keyboard.press('Tab')
  await expect(details).toBeFocused()
  const ring = await details.evaluate((e) => {
    const c = getComputedStyle(e)
    return c.outlineStyle !== 'none' && parseFloat(c.outlineWidth) > 0 ? 'outline' : c.boxShadow !== 'none' ? 'shadow' : 'none'
  })
  expect(ring, 'focus ring').not.toBe('none')
})

test('fold rule: only the first card of the tab is open the first time, and the choice is remembered', async ({ page }) => {
  await mockSources(page)
  await openManage(page)
  const card = (title: string) => page.locator(`details.section:has(> summary > .section-title:text-is("${title}"))`)
  await expect(card('All sites')).toHaveAttribute('open', '')
  await expect(card('Pacing')).not.toHaveAttribute('open', '')
  await expect(card('Saved pages')).not.toHaveAttribute('open', '')
  await expect(card('Advanced timing')).not.toHaveAttribute('open', '')
  await card('Pacing').locator(':scope > summary').click()
  await card('All sites').locator(':scope > summary').click()
  // The fold choice is saved from the details 'toggle' event, which fires after the click returns; reloading first loses it.
  await page.waitForFunction(() => ['sites.sites', 'timing.pacing'].every((k) => localStorage.getItem(`baihe.section.sources.manage.${k}`) !== null))
  await page.reload()
  await expect(page.getByRole('tab', { name: 'Manage sources' })).toHaveAttribute('aria-selected', 'true')
  await expect(card('Pacing')).toHaveAttribute('open', '')
  await expect(card('All sites')).not.toHaveAttribute('open', '')
})

test('Activity with nothing to list says so instead of showing a blank area', async ({ page }) => {
  const s = await mockSources(page)
  const manage = await openManage(page, 'activity')
  await expect(manage.getByText('Nothing yet. Pages you import from a link are listed here.')).toBeVisible()
  await expect(manage.locator('#sources-manage-activity details.section')).toHaveCount(0)
  expect(s.unmocked).toEqual([])
})
