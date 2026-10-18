import { expect, test } from './fixtures'

// A stage whose first read fails offers Retry, and the retry reads again.

test('Export readiness failure offers Retry that loads the checks', async ({ page }) => {
  let calls = 0
  await page.route('**/api/export/dramas/3/readiness', async (route) => {
    calls += 1
    if (calls === 1) await route.fulfill({ status: 500, json: { detail: 'boom' } })
    else await route.continue()
  })
  await page.goto('/#/drama/3/export')
  const retry = page.getByRole('button', { name: 'Retry' })
  await expect(retry).toBeVisible()
  await retry.click()
  await expect(page.getByTestId('readiness')).toBeVisible()
  await expect(retry).toHaveCount(0)
})

test('Translate settings failure offers Retry that loads the page', async ({ page }) => {
  let calls = 0
  await page.route('**/api/translate-run/dramas/3/config', async (route) => {
    calls += 1
    if (calls === 1) await route.fulfill({ status: 500, json: { detail: 'boom' } })
    else await route.continue()
  })
  await page.goto('/#/drama/3/translate')
  const retry = page.getByRole('button', { name: 'Retry' })
  await expect(retry).toBeVisible()
  await retry.click()
  await expect(retry).toHaveCount(0)
})
