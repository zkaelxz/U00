import { expect, test } from '@playwright/test'

test('hash routes show placeholders and the library is still the default', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('drama-count')).toHaveText('3 dramas')

  await page.goto('/#/settings')
  await expect(page.getByRole('region', { name: 'Settings' })).toBeVisible()

  await page.goto('/#/drama/2/review')
  await expect(page.getByRole('region', { name: 'Drama 2 workspace' })).toBeVisible()

  await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Library', exact: true }).click()
  await expect(page.getByTestId('drama-count')).toHaveText('3 dramas')
})
