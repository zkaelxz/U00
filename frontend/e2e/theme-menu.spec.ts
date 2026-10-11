import { expect, test, type Page } from '@playwright/test'
import { openSettingsGroups } from './settingsNav'

// The header theme button: Match this device / Light / Dark / Sepia / OLED black. The
// choice is per browser (localStorage), so nothing is sent to the API.

const bg = (page: Page) => page.evaluate(() => getComputedStyle(document.body).backgroundColor)
const html = (page: Page) => page.locator('html')
const button = (page: Page) => page.getByRole('button', { name: /^Theme: / })

async function pick(page: Page, label: string) {
  await button(page).click()
  await page.getByRole('menuitemradio', { name: label, exact: true }).click()
}

test.afterEach(async ({ page }) => {
  await page.evaluate(() => localStorage.removeItem('baihe.theme'))
})

test('each theme sets data-theme and changes the page colour', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light' })
  await page.goto('/#/library')
  await expect(button(page)).toHaveAccessibleName('Theme: Match this device. Change theme')
  await expect(html(page)).not.toHaveAttribute('data-theme', /.*/)
  const system = await bg(page)

  await pick(page, 'Dark')
  await expect(html(page)).toHaveAttribute('data-theme', 'dark')
  const dark = await bg(page)
  expect(dark).not.toBe(system)
  await expect(button(page)).toHaveAccessibleName('Theme: Dark. Change theme')

  await pick(page, 'Sepia')
  await expect(html(page)).toHaveAttribute('data-theme', 'sepia')
  const sepia = await bg(page)
  expect(sepia).toBe('rgb(239, 228, 203)')
  expect(sepia).not.toBe(system)
  expect(sepia).not.toBe(dark)

  await pick(page, 'Light')
  await expect(html(page)).toHaveAttribute('data-theme', 'light')
  expect(await bg(page)).toBe(system)

  // An explicit Light beats a dark device; Sepia beats it too (no dark tokens leak in).
  await page.emulateMedia({ colorScheme: 'dark' })
  expect(await bg(page)).toBe(system)
  await pick(page, 'Sepia')
  expect(await bg(page)).toBe(sepia)
  await pick(page, 'Match this device')
  await expect(html(page)).not.toHaveAttribute('data-theme', /.*/)
  expect(await bg(page)).toBe(dark)
})

test('the choice survives a reload and is set before the app renders', async ({ page }) => {
  await page.goto('/#/library')
  await pick(page, 'Sepia')
  await page.reload()
  await expect(html(page)).toHaveAttribute('data-theme', 'sepia')
  await expect(button(page)).toHaveAccessibleName('Theme: Sepia. Change theme')
  expect(await bg(page)).toBe('rgb(239, 228, 203)')

  // The inline script in index.html sets it while the page is still loading.
  await page.addInitScript(() => {
    document.addEventListener('DOMContentLoaded', () => {
      ;(window as unknown as { themeAtDcl: string | null }).themeAtDcl = document.documentElement.getAttribute('data-theme')
    })
  })
  await page.reload()
  expect(await page.evaluate(() => (window as unknown as { themeAtDcl: string | null }).themeAtDcl)).toBe('sepia')
})

test('the menu is keyboard operable and closes with Escape', async ({ page }) => {
  await page.goto('/#/library')
  const btn = button(page)
  await btn.focus()
  await expect(btn).toHaveAttribute('aria-expanded', 'false')
  await page.keyboard.press('Enter')
  await expect(btn).toHaveAttribute('aria-expanded', 'true')
  const menu = page.getByRole('menu', { name: 'Theme' })
  await expect(menu.getByRole('menuitemradio')).toHaveCount(5)
  // The current theme is focused and checked.
  await expect(page.getByRole('menuitemradio', { name: 'Match this device' })).toBeFocused()
  await expect(page.getByRole('menuitemradio', { name: 'Match this device' })).toHaveAttribute('aria-checked', 'true')

  await page.keyboard.press('Escape')
  await expect(menu).toHaveCount(0)
  await expect(btn).toBeFocused()

  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('End')
  await expect(page.getByRole('menuitemradio', { name: 'OLED black' })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(html(page)).toHaveAttribute('data-theme', 'oled')
  await expect(menu).toHaveCount(0)
  await expect(btn).toBeFocused()

  // A click elsewhere closes it too.
  await btn.click()
  await expect(menu).toBeVisible()
  await page.getByRole('heading', { name: 'Baihe Studio', level: 1 }).click()
  await expect(menu).toHaveCount(0)
})

test('Settings has no theme select; the header button is the only control', async ({ page }) => {
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'Translation and keys')
  await expect(page.getByRole('region', { name: 'Translation style', exact: true })).toBeVisible()
  await expect(page.getByLabel('Theme', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('region', { name: 'Appearance', exact: true })).toHaveCount(0)
  await pick(page, 'Sepia')
  await expect(html(page)).toHaveAttribute('data-theme', 'sepia')
})

test('OLED black sets a true-black page and is remembered', async ({ page }) => {
  await page.goto('/#/library')
  await pick(page, 'OLED black')
  await expect(html(page)).toHaveAttribute('data-theme', 'oled')
  expect(await bg(page)).toBe('rgb(0, 0, 0)')
  await expect(button(page)).toHaveAccessibleName('Theme: OLED black. Change theme')
  await page.waitForFunction(() => localStorage.getItem('baihe.theme') === 'oled')
  await page.reload()
  await expect(html(page)).toHaveAttribute('data-theme', 'oled')
  expect(await bg(page)).toBe('rgb(0, 0, 0)')
})

test('an unknown stored theme falls back to the system look', async ({ page }) => {
  await page.goto('/#/library')
  await page.evaluate(() => localStorage.setItem('baihe.theme', 'amoled'))
  await page.reload()
  await expect(html(page)).not.toHaveAttribute('data-theme', /.*/)
  await expect(button(page)).toHaveAccessibleName('Theme: Match this device. Change theme')
})
