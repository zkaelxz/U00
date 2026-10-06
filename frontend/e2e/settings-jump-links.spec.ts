import { expect, test } from '@playwright/test'

test.use({ storageState: { cookies: [], origins: [] } })

test('jump links open a folded settings section and scroll to it', async ({ page }) => {
  await page.goto('/#/settings')
  const nav = page.getByRole('navigation', { name: 'Jump to a settings section' })
  await expect(nav.getByRole('button')).toHaveCount(8)

  const notifications = page.getByRole('region', { name: 'Notifications' })
  await expect(notifications).toBeHidden() // folded by default
  const fold = page.locator('#settings-alerts')
  await expect(fold.locator('details.section').first()).not.toHaveAttribute('open', '')

  await nav.getByRole('button', { name: 'Notifications, backups, updates' }).click()
  await expect(fold.locator('details.section').first()).toHaveAttribute('open', '')
  await expect(notifications).toBeVisible()
  await expect(fold.locator('summary').first()).toBeFocused()
  // The app routes on the hash, so a jump must leave it alone.
  expect(new URL(page.url()).hash).toBe('#/settings')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})

test('an opened settings group stays open on the page but starts closed on the next visit', async ({ page }) => {
  await page.goto('/#/settings')
  const group = page.locator('#settings-advanced details.section').first()
  await group.locator('summary').first().click()
  await expect(group).toHaveAttribute('open', '')
  // Neither the open nor the closed state is remembered.
  expect(await page.evaluate(() => Object.keys(localStorage).filter((k) => k.startsWith('baihe.section.settings.')))).toEqual([])
  await page.reload()
  await expect(page.locator('#settings-advanced details.section').first()).not.toHaveAttribute('open', '')
})

test('every jump link reaches its group, including the three Jobs sections', async ({ page }) => {
  await page.goto('/#/settings')
  const nav = page.getByRole('navigation', { name: 'Jump to a settings section' })
  const ids = ['jobs', 'engines', 'defaults', 'alerts', 'sharing', 'integrations', 'advanced', 'experimental']
  const links = nav.getByRole('button')
  await expect(links).toHaveCount(ids.length)
  for (const [i, id] of ids.entries()) {
    await links.nth(i).click()
    await expect(page.locator(`#settings-${id} > details.section`)).toHaveAttribute('open', '')
    await expect(page.locator(`#settings-${id}`)).toBeInViewport()
  }
  const jobs = page.locator('#settings-jobs')
  for (const title of ['Performance', 'Notifications', 'Spending'])
    await expect(jobs.getByRole('heading', { name: title })).toBeVisible()
})
