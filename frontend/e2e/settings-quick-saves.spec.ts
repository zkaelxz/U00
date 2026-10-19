import { expect, test } from './fixtures'
import { openSettingsGroups } from './settingsNav'

test('two quick toggle saves keep both, even when the first answer arrives last', async ({ page }) => {
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'Performance and advanced')
  const a = page.getByRole('switch', { name: 'Free local-model memory before transcribing' })
  const b = page.getByRole('switch', { name: 'Resume translation batches on start' })
  const aBefore = await a.isChecked()
  const bBefore = await b.isChecked()

  // The first save is answered only after the second one, with the overview as it was before the second.
  let releaseFirst!: () => void
  const firstHeld = new Promise<void>((resolve) => (releaseFirst = resolve))
  let posts = 0
  await page.route('**/api/settings', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    const mine = ++posts
    const response = await route.fetch()
    if (mine === 1) await firstHeld
    await route.fulfill({ response })
  })

  await a.click()
  const secondSaved = page.waitForResponse((r) => r.url().endsWith('/api/settings') && r.request().method() === 'POST' && posts === 2)
  await b.click()
  await secondSaved
  releaseFirst()
  await expect(a).toBeChecked({ checked: !aBefore })
  await expect(b).toBeChecked({ checked: !bBefore })

  await page.unroute('**/api/settings')
  for (const [box, before] of [[a, aBefore], [b, bBefore]] as const) {
    const restored = page.waitForResponse((r) => r.url().endsWith('/api/settings') && r.request().method() === 'POST')
    await box.click()
    await restored
    await expect(box).toBeChecked({ checked: before })
  }
})
