import { expect, test } from '@playwright/test'
import { openSettingsGroups } from './settingsNav'

// Phone project (390x844): the Language packs card and an opened entries table fit the width.

test('Language packs fits a phone: no sideways page scroll with a pack open', async ({ page }) => {
  const long = 'A long set phrase that has to wrap rather than push the page sideways, again and again'
  await page.route('**/api/language-packs**', (route) => {
    const path = new URL(route.request().url()).pathname
    const summary = {
      id: 'ja-address', version: 1, language: 'ja', title: 'Japanese honorifics and address terms', description: long, entry_count: 1,
      styles: { default: '', options: {} },
    }
    if (path === '/api/language-packs') return route.fulfill({ json: [summary] })
    if (path.includes('/defaults/')) return route.fulfill({ json: { language: path.split('/').pop(), packs: ['ja-address'] } })
    return route.fulfill({ json: { ...summary, entries: [{ source: '先輩、これをお願いしてもよろしいでしょうか', en: long, category: 'phrase', note: long, context: '' }] } })
  })
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'Translation and keys')
  const card = page.getByRole('region', { name: 'Language packs', exact: true })
  await card.locator('summary').last().click()
  await expect(card.getByRole('table')).toContainText('先輩')
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)
  expect(overflow).toBe(false)
})
