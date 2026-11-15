import { expect, test } from './fixtures'
import { guardWrites } from './mockHelpers'

// People and access > Uploads from away (PC only): the switch moved here from Developer Mode but
// still needs Developer Mode, so with the mode off it can't be turned on and says where to.

test('Uploads from away sits on People and access and says it needs Developer Mode', async ({ page }) => {
  const unmocked = await guardWrites(page)
  await page.route('**/api/assistant/settings', (r) => r.request().method() === 'GET' ? r.fallback() : r.abort())
  await page.goto('/#/admin')
  const card = page.getByRole('region', { name: 'Uploads from away', exact: true })
  await expect(card).toBeVisible()
  await card.locator('> details > summary').click()
  await expect(card.getByRole('switch', { name: 'Allow uploads from away' })).toBeDisabled()
  await expect(card.getByRole('link', { name: 'Settings > Advanced' })).toHaveAttribute('href', '#/settings?section=developer-mode')
  expect(unmocked).toEqual([])
})
