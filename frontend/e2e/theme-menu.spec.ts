import { expect, test, type Page } from '@playwright/test'
import { openSettingsGroups } from './settingsNav'

// The theme choices in the header's You menu: Match this device / Light / Dark / Sepia / OLED black. The
// choice is per browser (localStorage), so nothing is sent to the API.

const bg = (page: Page) => page.evaluate(() => getComputedStyle(document.body).backgroundColor)
const html = (page: Page) => page.locator('html')
const you = (page: Page) => page.locator('details.you-menu')
const choice = (page: Page, label: string) => you(page).getByRole('radio', { name: label, exact: true })
const VALUE: Record<string, string> = { 'Match this device': 'system', Light: 'light', Dark: 'dark', Sepia: 'sepia', 'OLED black': 'oled' }
// Found by value, so it can be checked while the menu is closed.
const chosen = (page: Page, label: string) => you(page).locator(`input[name="theme"][value="${VALUE[label]}"]`)

async function pick(page: Page, label: string) {
  if (!(await you(page).evaluate((el) => (el as HTMLDetailsElement).open))) await you(page).locator('summary').click()
  await choice(page, label).check()
}

test.afterEach(async ({ page }) => {
  await page.evaluate(() => localStorage.removeItem('baihe.theme'))
})

test('each theme sets data-theme and changes the page colour', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light' })
  await page.goto('/#/library')
  await expect(chosen(page, 'Match this device')).toBeChecked()
  await expect(html(page)).not.toHaveAttribute('data-theme', /.*/)
  const system = await bg(page)

  await pick(page, 'Dark')
  await expect(html(page)).toHaveAttribute('data-theme', 'dark')
  const dark = await bg(page)
  expect(dark).not.toBe(system)
  await expect(chosen(page, 'Dark')).toBeChecked()

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
  await expect(chosen(page, 'Sepia')).toBeChecked()
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

test('the choices are keyboard operable and the menu closes with Escape', async ({ page }) => {
  await page.goto('/#/library')
  const summary = you(page).locator('summary')
  await summary.focus()
  await page.keyboard.press('Enter')
  await expect(you(page)).toHaveAttribute('open', '')
  await expect(you(page).getByRole('group', { name: 'Theme' }).getByRole('radio')).toHaveCount(5)
  await choice(page, 'Match this device').focus()
  await page.keyboard.press('ArrowDown')
  await expect(chosen(page, 'Light')).toBeChecked()
  await expect(html(page)).toHaveAttribute('data-theme', 'light')

  await page.keyboard.press('Escape')
  await expect(you(page)).not.toHaveAttribute('open', '')
  await expect(summary).toBeFocused()

  // A click elsewhere closes it too.
  await summary.click()
  await expect(you(page)).toHaveAttribute('open', '')
  await page.locator('.app-brand, .rail-brand').click()
  await expect(you(page)).not.toHaveAttribute('open', '')
})

test('Settings has no theme select; the You menu is the only control', async ({ page }) => {
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'Engines and keys')
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
  await expect(chosen(page, 'OLED black')).toBeChecked()
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
  await expect(chosen(page, 'Match this device')).toBeChecked()
})
