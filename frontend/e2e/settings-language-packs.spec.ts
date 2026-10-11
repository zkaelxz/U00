import { expect, test, type Page } from '@playwright/test'
import { openSettingsGroups } from './settingsNav'

// Settings > Translation and keys > Language packs: the read-only list of built-in packs per
// language, with which are on by default. The pack routes are mocked; any write is recorded.

const pack = (id: string, language: string, title: string, entry_count: number) => ({
  id, version: 1, language, title, description: `${title} description.`, entry_count,
  styles: { default: 'romanised', options: { romanised: 'Romanised', natural: 'Natural' } },
})

const PACKS = [
  pack('ja-address', 'ja', 'Japanese honorifics and address terms', 2),
  pack('ja-common', 'ja', 'Japanese kinship, roles and set phrases', 5),
  pack('ko-address', 'ko', 'Korean address terms', 3),
  pack('any-punct', 'any', 'Punctuation for every language', 4),
]
const DEFAULTS: Record<string, string[]> = { zh: [], ja: ['ja-address'], ko: [] }

async function mockPacks(page: Page) {
  const writes: string[] = []
  await page.route('**/api/language-packs**', (route) => {
    const r = route.request()
    const path = new URL(r.url()).pathname
    if (r.method() !== 'GET') {
      writes.push(`${r.method()} ${path}`)
      return route.abort()
    }
    if (path === '/api/language-packs') return route.fulfill({ json: PACKS })
    const lang = path.match(/\/defaults\/(\w+)$/)?.[1]
    if (lang) return route.fulfill({ json: { language: lang, packs: DEFAULTS[lang] ?? [] } })
    if (path.endsWith('/packs/ja-address')) {
      return route.fulfill({
        json: {
          ...PACKS[0],
          entries: [
            { source: '先輩', en: { romanised: 'senpai', natural: 'senior' }, category: 'honorific', note: 'Junior to senior.', context: '' },
            { source: 'さん', en: '-san', category: 'honorific', note: '', context: '' },
          ],
        },
      })
    }
    return route.fulfill({ status: 404, json: { error: { code: 'not_found', message: 'No such pack.' } } })
  })
  return writes
}

async function openCard(page: Page) {
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'Translation and keys')
  const card = page.getByRole('region', { name: 'Language packs', exact: true })
  await expect(card.getByRole('heading', { name: 'Japanese' })).toBeVisible()
  return card
}

test('lists the packs per language with entry counts and the default on or off', async ({ page }) => {
  const writes = await mockPacks(page)
  const card = await openCard(page)

  const ja = card.getByRole('region', { name: 'Japanese' })
  await expect(ja.locator('summary').nth(0)).toContainText('Japanese honorifics and address terms')
  await expect(ja.locator('summary').nth(0)).toContainText('2 entries · on by default')
  await expect(ja.locator('summary').nth(1)).toContainText('5 entries · off by default')
  await expect(card.getByRole('region', { name: 'Korean' }).locator('summary')).toContainText('3 entries · off by default')
  // No pack for Chinese, so no empty Chinese group.
  await expect(card.getByRole('region', { name: 'Chinese' })).toHaveCount(0)
  expect(writes).toEqual([])
})

test('a pack for any language shows no default status', async ({ page }) => {
  await mockPacks(page)
  const card = await openCard(page)
  const any = card.getByRole('region', { name: 'Any language' }).locator('summary')
  await expect(any).toContainText('Punctuation for every language')
  await expect(any).toContainText('4 entries')
  await expect(any).not.toContainText('by default')
})

test('opening a pack shows its read-only entries table', async ({ page }) => {
  const writes = await mockPacks(page)
  const card = await openCard(page)
  await card.locator('summary', { hasText: 'Japanese honorifics and address terms' }).click()
  const table = card.getByRole('table')
  await expect(table.getByRole('columnheader')).toHaveText(['Source', 'English', 'Note'])
  // A style-keyed entry shows the pack's default style; a plain string shows as is.
  await expect(table.getByRole('row', { name: /先輩/ })).toContainText('senpai')
  await expect(table.getByRole('row', { name: /先輩/ })).toContainText('Junior to senior.')
  await expect(table.getByRole('row', { name: /さん/ })).toContainText('-san')
  await expect(card.getByRole('button')).toHaveCount(0)
  await expect(card.locator('input, select')).toHaveCount(0)
  expect(writes).toEqual([])
})
