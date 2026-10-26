import { expect, test, type Page } from '@playwright/test'

import { clearDrama3Lines, python } from './reviewResultsSeed'

// Review open in two tabs of one browser, against the real API: what one tab
// saves must never be silently overwritten by the other's older copy. Nothing
// is mocked; drama 3 gets fresh lines before every test, as in review-stage.spec.ts.

test.beforeEach(() => {
  python(`
from core import Line
db.update_drama(3, audio_filename=None, source_video_filename=None)
db.save_lines(3, [
    Line(idx=0, start=0.0, end=1.5, zh='你好', en='Hello there'),
    Line(idx=1, start=1.5, end=3.0, zh='再见朋友', en='Goodbye, friend'),
    Line(idx=2, start=3.0, end=4.5, zh='谢谢', en='Thanks, friend'),
])
`)
})

const savedEn = () => JSON.parse(python('import json\nprint(json.dumps([l["en"] for l in db.load_lines(3)]))')) as string[]

test.afterEach(() => clearDrama3Lines())

const rows = (page: Page) => page.locator('.review-line:not(.review-skeleton)')

async function open(page: Page, count = 3) {
  await page.goto('/#/drama/3/review')
  await expect(rows(page)).toHaveCount(count)
}

// The tab being used comes to the front, as it would for a person switching tabs.
async function saveEdit(page: Page, n: number, text: string) {
  await page.bringToFront()
  const row = rows(page).nth(n)
  await row.getByTestId('line-en').click()
  await row.getByLabel('Translation').fill(text)
  const saved = page.waitForResponse((r) => /\/api\/lines\/dramas\/3\/lines\/\d+$/.test(r.url()) && r.request().method() === 'POST')
  await row.getByRole('button', { name: 'Save', exact: true }).click()
  const response = await saved
  if (response.ok()) await row.getByLabel('Translation').press('Escape')
  return { row, response }
}

const LINE_SAVE = /\/api\/lines\/dramas\/3\/lines\/\d+$/

test('the same line edited in two tabs: the older copy is refused, and Reload shows the other tab\'s text', async ({ context }) => {
  const a = await context.newPage()
  const b = await context.newPage()
  await open(a)
  await open(b)

  const first = await saveEdit(a, 1, 'Farewell from tab A')
  expect(first.response.status()).toBe(200)
  await expect(first.row.getByTestId('line-en')).toContainText('Farewell from tab A')

  const second = await saveEdit(b, 1, 'Farewell from tab B')
  expect(second.response.status()).toBe(409)
  await expect(second.row.getByTestId('line-conflict')).toContainText('changed elsewhere')
  await expect(second.row.getByLabel('Translation')).toHaveValue('Farewell from tab B')
  expect(savedEn()[1]).toBe('Farewell from tab A')

  // Reload shows what tab A saved in the field both typed in (rebaseAfterReload in useLinesLoad.ts),
  // so nothing is saved over it unseen; the edit stays open to type again.
  await second.row.getByTestId('line-conflict').getByRole('button', { name: 'Reload' }).click()
  await expect(second.row.getByTestId('line-conflict')).toHaveCount(0)
  await expect(second.row.getByLabel('Translation')).toHaveValue('Farewell from tab A')
  await second.row.getByLabel('Translation').fill('Farewell, typed again in B')
  const resaved = b.waitForResponse((r) => LINE_SAVE.test(r.url()) && r.request().method() === 'POST')
  await second.row.getByRole('button', { name: 'Save', exact: true }).click()
  expect((await resaved).status()).toBe(200)
  expect(savedEn()).toEqual(['Hello there', 'Farewell, typed again in B', 'Thanks, friend'])
})

test('two tabs changing different fields of one line both save', async ({ context }) => {
  const a = await context.newPage()
  const b = await context.newPage()
  await open(a)
  await open(b)
  expect((await saveEdit(a, 0, 'Hi from A')).response.status()).toBe(200)

  await b.bringToFront()
  const row = rows(b).nth(0)
  await row.locator('.review-idx').click()
  await row.getByRole('button', { name: 'Edit details' }).click()
  await row.getByLabel('Source', { exact: true }).fill('你好啊')
  const saved = b.waitForResponse((r) => LINE_SAVE.test(r.url()) && r.request().method() === 'POST')
  await row.getByRole('button', { name: 'Save', exact: true }).click()
  expect((await saved).status()).toBe(200)
  const zhEn = JSON.parse(python('import json\nl = db.load_lines(3)[0]\nprint(json.dumps([l["zh"], l["en"]]))'))
  expect(zhEn).toEqual(['你好啊', 'Hi from A'])
})

test('different lines edited in two tabs both save', async ({ context }) => {
  const a = await context.newPage()
  const b = await context.newPage()
  await open(a)
  await open(b)
  expect((await saveEdit(a, 0, 'Hi from A')).response.status()).toBe(200)
  expect((await saveEdit(b, 2, 'Thanks from B')).response.status()).toBe(200)
  expect(savedEn()).toEqual(['Hi from A', 'Goodbye, friend', 'Thanks from B'])
})

test('a merge in one tab: the other tab\'s merge is stopped before it is sent, until it reloads', async ({ context }) => {
  const a = await context.newPage()
  const b = await context.newPage()
  await open(a)
  await open(b)
  const merges: string[] = []
  context.on('request', (r) => {
    if (r.method() === 'POST' && r.url().includes('/api/restructure/dramas/3/merge')) merges.push(r.url())
  })

  async function openMerge(page: Page) {
    await page.bringToFront()
    const row = rows(page).nth(0)
    await row.locator('.review-idx').click()
    await expect(row).toHaveAttribute('aria-current', 'true')
    await page.keyboard.press('m')
    return page.getByRole('dialog', { name: /Merge #\d+ with next/ })
  }

  const done = a.waitForResponse((r) => r.url().includes('/api/restructure/dramas/3/merge'))
  await (await openMerge(a)).getByRole('button', { name: /^Merge #/ }).click()
  expect((await done).status()).toBe(200)
  await expect(rows(a)).toHaveCount(2)

  // Tab B still shows three lines: it checks the current list first and refuses.
  const dialog = await openMerge(b)
  await dialog.getByRole('button', { name: /^Merge #/ }).click()
  await expect(dialog).toContainText('Lines changed since this page loaded. Reload and try again.')
  expect(merges).toHaveLength(1)
  expect(savedEn()).toEqual(['Hello there Goodbye, friend', 'Thanks, friend'])
  await b.reload()
  await expect(rows(b)).toHaveCount(2)
})
