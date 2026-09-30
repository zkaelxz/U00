import { expect, test } from '@playwright/test'

// Phone: each Settings switch has a >=44px-tall hit area (the ::after overlay)
// and the page has no sideways scroll.
test('settings switches have 44px touch targets on a phone', async ({ page }) => {
  await page.goto('/#/settings')
  await expect(page.getByRole('region', { name: 'Jobs' }).getByRole('switch')).toHaveCount(5)
  await expect(page.getByRole('switch', { name: 'Extension bridge' })).toBeVisible() // the seeded API leaves the bridge off or on; either way it is a switch
  const switches = page.getByRole('switch')
  for (const sw of await switches.all()) {
    // elementFromPoint only sees the viewport; centre it so the +-21px probes stay on screen.
    await sw.evaluate((el) => el.scrollIntoView({ block: 'center' }))
    const hit = await sw.evaluate((el) => {
      const r = el.getBoundingClientRect()
      const cx = r.left + r.width / 2
      const cy = r.top + r.height / 2
      const at = (y: number) => el.contains(document.elementFromPoint(cx, y))
      return { top: at(cy - 21), bottom: at(cy + 21), width: r.width }
    })
    expect(hit).toEqual({ top: true, bottom: true, width: 44 })
  }
  // "Set key" buttons in the key rows are dense (.btn-sm) but still 44px on touch.
  for (const b of await page.getByRole('region', { name: 'API keys' }).getByRole('button').all())
    expect((await b.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})
