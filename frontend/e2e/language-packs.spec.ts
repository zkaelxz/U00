import { expect, test, type Page } from '@playwright/test'
import { inSeriesSeven, mockLanguagePacks } from './languagePacksMocks'

// Glossary > Language packs: off by default, a toggle and style per pack, entries
// read-only, and Add to my glossary. The pack routes and glossary writes are mocked.

test.use({ viewport: { width: 1280, height: 800 } })

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

async function openPacks(page: Page) {
  await page.goto('/#/drama/1/translate')
  for (const title of ['Glossary', 'Language packs']) {
    const summary = page.locator('summary').filter({ has: page.locator('.section-title', { hasText: new RegExp(`^${title}$`) }) }).first()
    if ((await summary.locator('xpath=..').getAttribute('open')) === null) await summary.click()
  }
}

test('lists the packs off by default and says they are drafts', async ({ page }) => {
  await inSeriesSeven(page)
  await mockLanguagePacks(page)
  await openPacks(page)
  await expect(page.getByText('Starter packs, review them.')).toBeVisible()
  await expect(page.getByText('Japanese honorifics and address terms')).toBeVisible()
  await expect(page.getByText('Version 1 · 2 entries').first()).toBeVisible()
  await expect(page.getByRole('switch', { name: 'Use Japanese honorifics and address terms for this title' })).toHaveAttribute('aria-checked', 'false')
})

test('a toggle and the style choice are saved for the title', async ({ page }) => {
  await inSeriesSeven(page)
  const seen = await mockLanguagePacks(page)
  await openPacks(page)
  await page.getByRole('switch', { name: 'Use Japanese honorifics and address terms for this title' }).click()
  await expect.poll(() => seen.posts.length).toBe(1)
  expect(seen.posts[0]).toEqual({ packs: { 'ja-address': 'romanised' } })
  await page.getByLabel('How to write them').selectOption('natural')
  await expect.poll(() => seen.posts.length).toBe(2)
  expect(seen.posts[1]).toEqual({ packs: { 'ja-address': 'natural' } })
})

test('entries are read-only and Add to my glossary copies one in the current style', async ({ page }) => {
  await inSeriesSeven(page)
  const seen = await mockLanguagePacks(page)
  await openPacks(page)
  await page.getByText('View entries (2)').first().click()
  await expect(page.getByText('senpai', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Add 先輩 to my glossary' }).click()
  await expect.poll(() => seen.terms.length).toBe(1)
  expect(seen.terms[0]).toMatchObject({ term_original: '先輩', term_translation: 'senpai', category: 'honorific' })
  await expect(page.getByRole('button', { name: 'Add さん to my glossary' })).toBeEnabled()
})

test('the default for the language is saved', async ({ page }) => {
  await inSeriesSeven(page)
  const seen = await mockLanguagePacks(page)
  await openPacks(page)
  await page.getByRole('switch', { name: 'Use Japanese honorifics and address terms for this title' }).click()
  await page.getByRole('button', { name: 'Use this choice for Japanese titles' }).click()
  await expect(page.getByText('Saved. Japanese titles without their own choice now use these packs.')).toBeVisible()
  expect(seen.defaults).toEqual([{ packs: { 'ja-address': 'romanised' } }])
})

test('toggling two packs in quick succession keeps both on', async ({ page }) => {
  await inSeriesSeven(page)
  const seen = await mockLanguagePacks(page)
  await openPacks(page)
  const a = page.getByRole('switch', { name: 'Use Japanese honorifics and address terms for this title' })
  const b = page.getByRole('switch', { name: 'Use Japanese kinship, roles and set phrases for this title' })
  await a.click()
  await b.click()
  await expect.poll(() => seen.posts.length).toBe(2)
  expect(Object.keys((seen.posts[1] as { packs: object }).packs).sort()).toEqual(['ja-address', 'ja-common'])
  await expect(a).toHaveAttribute('aria-checked', 'true')
  await expect(b).toHaveAttribute('aria-checked', 'true')
})
