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
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})
