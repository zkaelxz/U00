import { zipSync } from 'fflate'
import { expect, test, type Page } from '@playwright/test'

import { json } from './mockHelpers'
import { mockScanlate } from './scanlateMocks'

// The Add a Comic dialog on the comic page, one test per way in. Every /api call is mocked (scanlateMocks); the
// new title is #7, the title the mocks serve, so opening it needs no second set of mocks.

const NEW = { id: 7, title_en: 'Solo', media_type: 'manhwa' }

async function setup(page: Page) {
  const s = await mockScanlate(page, { mediaType: 'manhwa' })
  const created: unknown[] = []
  const links: unknown[] = []
  const uploaded: string[][] = []
  await page.route('**/api/dramas', (route) => {
    created.push(route.request().postDataJSON())
    return json(route, NEW)
  })
  await page.route('**/api/sources/url/import-comic', (route) => {
    links.push(route.request().postDataJSON())
    return json(route, { job_id: 'sourceimport_7' })
  })
  // Replaces the mock's counter-only upload route to read which files were sent.
  await page.route(/\/api\/scanlate\/dramas\/7\/pages$/, async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    const body = route.request().postDataBuffer()?.toString('latin1') ?? ''
    uploaded.push([...body.matchAll(/filename="([^"]+)"/g)].map((m) => m[1]))
    return json(route, { added: uploaded.at(-1)!.length, page_ids: [], pdf_pages_skipped: 0, strips_sliced: 0 })
  })
  await page.goto('/#/comic/7?page=1')
  await page.getByRole('button', { name: 'Add a Comic' }).click()
  const dialog = page.getByRole('dialog', { name: 'Add a Comic' })
  await expect(dialog).toBeVisible()
  await dialog.getByLabel('Title', { exact: true }).fill('Solo')
  await dialog.getByLabel('Language').selectOption('ko')
  return { s, dialog, created, links, uploaded }
}

const savedMode = (page: Page) =>
  page.evaluate(() => JSON.parse(window.localStorage.getItem('baihe.pref.comic.view.7') ?? 'null')?.mode ?? null)

test('Add stays off until the chosen way has what it needs', async ({ page }) => {
  const { dialog } = await setup(page)
  const add = dialog.getByRole('button', { name: 'Add', exact: true })
  await expect(add).toBeDisabled()
  await dialog.getByLabel('Link', { exact: true }).fill('not a link')
  await expect(add).toBeDisabled()
  await dialog.getByLabel('Link', { exact: true }).fill('https://comic.example/series/9')
  await expect(add).toBeEnabled()
  await dialog.getByRole('tab', { name: 'Images' }).click()
  await expect(add).toBeDisabled()
})

test('paste a link: makes the title and imports the link, reading in the chosen mode', async ({ page }) => {
  const { s, dialog, created, links } = await setup(page)
  await dialog.getByLabel('Link', { exact: true }).fill('https://comic.example/series/9')
  await dialog.getByLabel('Reading mode').selectOption('paged')
  await dialog.getByRole('button', { name: 'Add', exact: true }).click()
  await expect.poll(() => links).toEqual([{ url: 'https://comic.example/series/9', drama_id: 7 }])
  expect(created).toEqual([{ source_language: 'ko', title_en: 'Solo', media_type: 'manhwa' }])
  await expect(dialog).toBeHidden()
  expect(await savedMode(page)).toBe('paged')
  expect(s.unmocked).toEqual([])
})

test('upload images: the picked pages go to the new title', async ({ page }) => {
  const { s, dialog, created, uploaded } = await setup(page)
  await dialog.getByRole('tab', { name: 'Images' }).click()
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==', 'base64')
  await dialog.locator('input[type=file]').setInputFiles([
    { name: '1.png', mimeType: 'image/png', buffer: png },
    { name: '2.png', mimeType: 'image/png', buffer: png },
  ])
  await dialog.getByRole('button', { name: 'Add', exact: true }).click()
  await expect.poll(() => uploaded).toEqual([['1.png', '2.png']])
  expect(created).toHaveLength(1)
  await expect(dialog).toBeHidden()
  expect(s.unmocked).toEqual([])
})

test('import a CBZ: its pages are sent in reading order', async ({ page }) => {
  const { s, dialog, uploaded } = await setup(page)
  await dialog.getByRole('tab', { name: 'CBZ' }).click()
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==', 'base64')
  const cbz = Buffer.from(zipSync({
    'c/p10.png': new Uint8Array(png), 'c/p2.png': new Uint8Array(png), 'c/p1.png': new Uint8Array(png), 'c/info.xml': new Uint8Array([1]),
  }))
  await dialog.locator('input[type=file]').setInputFiles({ name: 'ch1.cbz', mimeType: 'application/zip', buffer: cbz })
  await dialog.getByRole('button', { name: 'Add', exact: true }).click()
  await expect.poll(() => uploaded).toEqual([['p1.png', 'p2.png', 'p10.png']])
  await expect(dialog).toBeHidden()
  expect(s.unmocked).toEqual([])
})

test('a refused link keeps the dialog open and a second Add reuses the title', async ({ page }) => {
  const { dialog, created } = await setup(page)
  let tries = 0
  await page.route('**/api/sources/url/import-comic', (route) => {
    tries += 1
    return tries === 1
      ? json(route, { error: { code: 'forbidden', message: 'Not allowed.' } }, 403)
      : json(route, { job_id: 'sourceimport_7' })
  })
  await dialog.getByLabel('Link', { exact: true }).fill('https://comic.example/series/9')
  const add = dialog.getByRole('button', { name: 'Add', exact: true })
  await add.click()
  await expect(dialog.getByRole('alert')).toBeVisible()
  await add.click()
  await expect(dialog).toBeHidden()
  expect(created).toHaveLength(1)
})
