import { expect, test } from '@playwright/test'

// Discover and Sources are one rail entry, Find; each page carries a switch to the other, and the old addresses still load.
test('the Find switch moves between Discover and Sources and the rail entry stays lit', async ({ page }) => {
  await page.goto('/#/discover')
  const rail = page.getByRole('navigation', { name: 'Main' })
  await expect(rail.getByRole('link', { name: 'Find' })).toHaveAttribute('aria-current', 'page')
  const find = page.getByRole('navigation', { name: 'Find', exact: true })
  await expect(find.getByRole('link', { name: 'Discover' })).toHaveAttribute('aria-current', 'page')
  await find.getByRole('link', { name: 'Sources' }).click()
  await expect(page).toHaveURL(/#\/sources$/)
  await expect(page.getByRole('heading', { level: 1, name: 'Sources' })).toBeVisible()
  await expect(rail.getByRole('link', { name: 'Find' })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('navigation', { name: 'Find', exact: true }).getByRole('link', { name: 'Sources' })).toHaveAttribute('aria-current', 'page')
})
