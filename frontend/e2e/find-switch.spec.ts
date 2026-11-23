import { expect, test } from '@playwright/test'

// Discover and Sources are one rail entry, Find; Discover carries a switch to Sources, and Sources links back from Paste a link.
test('the Find switch moves between Discover and Sources and the rail entry stays lit', async ({ page }) => {
  await page.goto('/#/discover')
  const rail = page.getByRole('navigation', { name: 'Main' })
  await expect(rail.getByRole('link', { name: 'Find' })).toHaveAttribute('aria-current', 'page')
  const find = page.getByRole('navigation', { name: 'Find', exact: true })
  await expect(find.getByRole('link', { name: 'Discover' })).toHaveAttribute('aria-current', 'page')
  await find.getByRole('link', { name: 'Sources' }).click()
  await expect(page).toHaveURL(/#\/sources$/)
  await expect(page.getByRole('heading', { level: 1, name: 'Find' })).toBeVisible()
  await expect(rail.getByRole('link', { name: 'Find' })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('navigation', { name: 'Find', exact: true })).toHaveCount(0)
  await page.getByRole('radio', { name: 'Paste a link' }).check()
  await page.getByRole('link', { name: 'Discover', exact: true }).click()
  await expect(page).toHaveURL(/#\/discover$/)
})
