import { expect, test } from '@playwright/test'

// Find & Add is one rail entry with four tabs, Add, Discover, Sources and Updates. Each tab is its own address, so an
// old link opens its tab and the rail entry stays lit on all four.
test('the Find & Add tabs move between Add, Discover, Sources and Updates and the rail entry stays lit', async ({ page }) => {
  await page.goto('/#/discover')
  const rail = page.getByRole('navigation', { name: 'Main' })
  const tabs = page.getByRole('navigation', { name: 'Find & Add', exact: true })
  await expect(page.getByRole('heading', { level: 1, name: 'Find & Add' })).toBeVisible()
  await expect(rail.getByRole('link', { name: 'Find & Add' })).toHaveAttribute('aria-current', 'page')
  await expect(rail.getByRole('link', { name: 'Updates' })).toHaveCount(0)
  await expect(tabs.getByRole('link')).toHaveText(['Add', 'Discover', 'Sources', 'Updates'])
  await expect(tabs.getByRole('link', { name: 'Discover' })).toHaveAttribute('aria-current', 'page')

  for (const [name, url] of [['Sources', /#\/sources$/], ['Updates', /#\/updates$/], ['Add', /#\/make-subtitles$/]] as const) {
    await tabs.getByRole('link', { name, exact: true }).click()
    await expect(page).toHaveURL(url)
    await expect(page.getByRole('heading', { level: 1, name: 'Find & Add' })).toBeVisible()
    await expect(tabs.getByRole('link', { name, exact: true })).toHaveAttribute('aria-current', 'page')
    await expect(tabs.locator('[aria-current="page"]')).toHaveCount(1)
    await expect(rail.getByRole('link', { name: 'Find & Add' })).toHaveAttribute('aria-current', 'page')
  }

  // Sources' Paste a link still points to Discover.
  await tabs.getByRole('link', { name: 'Sources', exact: true }).click()
  await page.getByRole('radio', { name: 'Paste a link' }).check()
  await page.getByRole('main').getByRole('link', { name: 'Discover', exact: true }).last().click()
  await expect(page).toHaveURL(/#\/discover$/)
})
