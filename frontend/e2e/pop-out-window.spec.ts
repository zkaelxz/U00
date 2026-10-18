import { expect, test } from '@playwright/test'

test('Open in new window gives a slim second window that browses on its own', async ({ page, context }) => {
  await page.goto('/#/drama/1/review')
  await expect(page.getByTestId('drama-title')).toBeVisible()
  await expect(page.locator('.app-rail')).toBeVisible()

  const [popup] = await Promise.all([context.waitForEvent('page'), page.getByTestId('open-in-window').click()])
  await popup.waitForLoadState()
  await expect(popup).toHaveURL(/#\/drama\/1\/review$/)
  await expect(popup.getByTestId('drama-title')).toBeVisible()
  // No side nav and no second copy of the button; the header's page menu stands in for the nav.
  await expect(popup.locator('.app-rail')).toHaveCount(0)
  await expect(popup.getByTestId('open-in-window')).toHaveCount(0)

  await popup.locator('.popout-menu > summary').click()
  await popup.getByRole('navigation', { name: 'Pages' }).getByRole('link', { name: 'Settings' }).click()
  await expect(popup).toHaveURL(/#\/settings$/)
  await expect(popup.getByRole('heading', { level: 1 })).toBeVisible()

  // Each window has its own page: the first one stayed on the title.
  await expect(page).toHaveURL(/#\/drama\/1\/review$/)
  await expect(page.getByTestId('drama-title')).toBeVisible()

  // A reload keeps the slim layout.
  await popup.reload()
  await expect(popup.locator('.popout-menu')).toBeVisible()
})
