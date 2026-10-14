import { expect, test } from '@playwright/test'

test('Back appears after an in-app navigation and returns to the previous page', async ({ page }) => {
  await page.goto('/#/jobs')
  // The first page is numbered when the app mounts, so let it mount before navigating on.
  await expect(page.locator('#main-content')).toBeVisible()
  const back = page.getByRole('button', { name: 'Go back to the previous page' })
  await expect(back).toHaveCount(0)
  await page.goto('/#/settings')
  await expect(back).toBeVisible()
  await back.click()
  await expect(page).toHaveURL(/#\/jobs$/)
  await expect(back).toHaveCount(0)
})
