import { expect, test } from './fixtures'
import { guardWrites } from './mockHelpers'

// People and access > Remote access > Uploads from away (PC only): a switch off until turned on, plus an opt-in 7-day auto-off, sent through the settings save.

test('Uploads from away is a switch on People and access that saves remote_uploads', async ({ page }) => {
  const unmocked = await guardWrites(page)
  const bodies: unknown[] = []
  await page.route('**/api/settings', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    bodies.push(route.request().postDataJSON())
    const current = await (await page.request.get('/api/settings')).json()
    return route.fulfill({ json: { ...current, remote_uploads: true, remote_uploads_auto_off: false } })
  })
  await page.goto('/#/admin?card=uploads')
  await expect(page.getByRole('heading', { name: 'Uploads from away', level: 2 })).toBeVisible()
  const sw = page.getByRole('switch', { name: 'Allow uploads from away' })
  await expect(sw).toHaveAttribute('aria-checked', 'false')
  await sw.click()
  await expect(sw).toHaveAttribute('aria-checked', 'true')
  expect(bodies).toEqual([{ remote_uploads: true }])
  await expect(page.getByRole('switch', { name: 'Turn off automatically after 7 days' })).toHaveAttribute('aria-checked', 'false')
  expect(unmocked).toEqual([])
})
