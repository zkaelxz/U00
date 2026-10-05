import { expect, test, type Page } from '@playwright/test'

// The drawer below 1024px at a desktop browser's narrow window, and the
// hand-over to the rail when the window grows past 1024px.

const menuButton = (page: Page) => page.getByRole('button', { name: 'Menu', exact: true })
const drawer = (page: Page) => page.getByRole('dialog', { name: 'Main menu' })

test.describe('1000px wide', () => {
  test.use({ viewport: { width: 1000, height: 800 } })

  test('open, Tab stays inside, Esc closes and focus returns to the button', async ({ page }) => {
    await page.goto('/#/library')
    await expect(page.getByRole('complementary')).toHaveCount(0)
    await menuButton(page).click()
    await expect(menuButton(page)).toHaveAttribute('aria-expanded', 'true')
    await expect(drawer(page)).toBeVisible()
    for (let i = 0; i < 20; i++) {
      await page.keyboard.press('Tab')
      expect(await page.evaluate(() => !!document.activeElement?.closest('dialog.nav-drawer')), `Tab ${i}`).toBe(true)
    }
    await page.keyboard.press('Escape')
    await expect(drawer(page)).toBeHidden()
    await expect(menuButton(page)).toBeFocused()
  })

  test('the backdrop closes it and a link navigates and closes', async ({ page }) => {
    await page.goto('/#/library')
    await menuButton(page).click()
    await expect(drawer(page)).toBeVisible()
    await page.mouse.click(700, 400)
    await expect(drawer(page)).toBeHidden()
    await menuButton(page).click()
    await drawer(page).getByRole('link', { name: 'Settings', exact: true }).click()
    await expect(page).toHaveURL(/#\/settings$/)
    await expect(drawer(page)).toBeHidden()
  })

  test('crossing 1024px keeps the open page mounted and swaps the drawer for the rail', async ({ page }) => {
    await page.goto('/#/library')
    await menuButton(page).click()
    await expect(drawer(page)).toBeVisible()
    await page.evaluate(() => {
      const page = document.querySelector('.app-main > :not(header):not(.remote-health-banner)')!
      ;(page as unknown as { __mark: string }).__mark = 'same node'
    })
    await page.setViewportSize({ width: 1280, height: 800 })
    await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible()
    await expect(menuButton(page)).toHaveCount(0)
    await expect(drawer(page)).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.classList.contains('nav-locked'))).toBe(false)
    expect(await page.evaluate(() => (document.querySelector('.app-main > :not(header):not(.remote-health-banner)') as unknown as { __mark?: string }).__mark)).toBe('same node')
    await page.setViewportSize({ width: 1000, height: 800 })
    await expect(menuButton(page)).toBeVisible()
  })
})
