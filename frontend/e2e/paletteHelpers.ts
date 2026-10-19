import { expect, type Page } from '@playwright/test'

import { hideDiscoverFromSettings } from './customizeMenu'

export const searchButton = (page: Page) => page.getByRole('button', { name: 'Quick search', exact: true })
export const palette = (page: Page) => page.getByRole('dialog', { name: 'Quick search' })
export const paletteInput = (page: Page) => palette(page).getByRole('combobox')

// Typing, Enter and the hidden-item rule, shared by the desktop and phone specs; `open` is how each project opens the palette.
export async function expectFilterAndEnter(page: Page, open: () => Promise<void>) {
  await page.goto('/#/library')
  await open()
  await expect(palette(page).getByRole('option', { name: /^Discover/ })).toBeVisible()
  await paletteInput(page).fill('JOB')
  // Settings cards that mention jobs are listed too; the page itself is still the one page hit and ranks first.
  await expect(palette(page).getByRole('option', { name: /Page$/ })).toHaveCount(1)
  await expect(palette(page).getByRole('option').first()).toHaveText(/^Jobs/)
  await expect(paletteInput(page)).toHaveAttribute('aria-activedescendant', /.+/)
  await paletteInput(page).press('Enter')
  await expect(page).toHaveURL(/#\/jobs$/)
  await expect(palette(page)).toBeHidden()
}

export async function expectNoMatches(page: Page, open: () => Promise<void>) {
  await page.goto('/#/library')
  await open()
  await paletteInput(page).fill('zzzzqq')
  await expect(palette(page).getByRole('status')).toContainText('No matches')
  await expect(palette(page).getByRole('option')).toHaveCount(0)
}

export async function expectHiddenItemNotListed(page: Page, open: () => Promise<void>) {
  await hideDiscoverFromSettings(page)
  await open()
  await expect(palette(page).getByRole('option', { name: /^Library Page/ })).toBeVisible()
  await expect(palette(page).getByRole('option', { name: /^Settings Page/ })).toBeVisible()
  await expect(palette(page).getByRole('option', { name: /^Discover/ })).toHaveCount(0)
  await paletteInput(page).fill('discover')
  await expect(palette(page).getByRole('status')).toContainText('No matches')
  // Hiding is tidiness only: the address still opens.
  await page.keyboard.press('Escape')
  await page.goto('/#/discover')
  await expect(page.getByRole('heading', { name: 'Discover' }).first()).toBeVisible()
}
