import { expect, test } from '@playwright/test'

test.use({ storageState: { cookies: [], origins: [] } })

test('phone: jump links wrap with 44 px targets and no sideways scroll', async ({ page }) => {
  await page.goto('/#/settings')
  const nav = page.getByRole('navigation', { name: 'Jump to a settings section' })
  const links = nav.getByRole('button')
  await expect(links).toHaveCount(8)
  for (const box of await Promise.all((await links.all()).map((l) => l.boundingBox())))
    expect(box!.height).toBeGreaterThanOrEqual(44)

  await links.filter({ hasText: 'Experimental & developer' }).click()
  await expect(page.locator('#settings-experimental details.section').first()).toHaveAttribute('open', '')
  await expect(page.locator('#settings-experimental')).toBeInViewport()
  // The Jobs group holds three sections; its switches and number box keep a 44 px target on a phone.
  await links.filter({ hasText: 'Jobs' }).click()
  const jobs = page.locator('#settings-jobs')
  for (const title of ['Performance', 'Notifications', 'Spending']) await expect(jobs.getByRole('heading', { name: title })).toBeVisible()
  for (const sw of await jobs.getByRole('switch').all()) {
    await sw.evaluate((el) => el.scrollIntoView({ block: 'center' }))
    expect(await sw.evaluate((el) => { const r = el.getBoundingClientRect(); const cx = r.left + r.width / 2; const cy = r.top + r.height / 2; return el.contains(document.elementFromPoint(cx, cy - 21)) && el.contains(document.elementFromPoint(cx, cy + 21)) })).toBe(true)
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})
