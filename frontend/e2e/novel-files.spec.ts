import { expect, test, type Page } from '@playwright/test'

// Novel reference (Translate) and raw novel (Transcribe, on the Source page):
// the novel-files endpoints are mocked with an in-memory store, and every
// mock fulfils. Everything else hits the real seeded API.

type Status = { drama_id: number; present: boolean; size_bytes: number; char_count: number }
const empty = (): Status => ({ drama_id: 1, present: false, size_bytes: 0, char_count: 0 })

async function mockNovelFiles(page: Page, opts: { uploadStatus?: number } = {}) {
  const store = { reference: empty(), 'raw-novel': empty() }
  const uploads: { kind: string; local: string | null; body: string }[] = []
  for (const kind of ['reference', 'raw-novel'] as const) {
    await page.route(`**/api/novel/dramas/1/${kind}`, async (route) => {
      const req = route.request()
      if (req.method() === 'GET') return route.fulfill({ json: store[kind] })
      uploads.push({ kind, local: await req.headerValue('x-baihe-local'), body: req.postData() ?? '' })
      if (opts.uploadStatus) {
        return route.fulfill({
          status: opts.uploadStatus,
          json: { error: { code: 'conflict', message: 'A job is running for this drama. Wait for it to finish or cancel it.' } },
        })
      }
      const replaced = store[kind].present
      store[kind] = { drama_id: 1, present: true, size_bytes: 2048, char_count: 1500 }
      return route.fulfill({ json: { ...store[kind], replaced } })
    })
  }
  const pastes: { kind: string; local: string | null; json: unknown }[] = []
  for (const kind of ['reference', 'raw-novel'] as const) {
    await page.route(`**/api/novel/dramas/1/${kind}/text`, async (route) => {
      const req = route.request()
      const json = req.postDataJSON() as { text: string }
      pastes.push({ kind, local: await req.headerValue('x-baihe-local'), json })
      const replaced = store[kind].present
      store[kind] = { drama_id: 1, present: true, size_bytes: json.text.length, char_count: json.text.trim().length }
      return route.fulfill({ json: { ...store[kind], replaced } })
    })
  }
  await page.route('**/api/novel/dramas/1/reference/remove', (route) => {
    store.reference = empty()
    return route.fulfill({ json: { drama_id: 1, removed: true, present: false } })
  })
  await page.route('**/api/novel/dramas/1/raw-novel/remove', (route) => {
    store['raw-novel'] = empty()
    return route.fulfill({ json: { drama_id: 1, removed: true, has_raw_novel_context: false } })
  })
  return { store, uploads, pastes }
}

const txt = (name = 'novel.txt') => ({ name, mimeType: 'text/plain', buffer: Buffer.from('Chapter 1') })

test('Translate: upload, replace and remove the English novel reference', async ({ page }) => {
  const { uploads } = await mockNovelFiles(page)
  await page.goto('/#/drama/1/translate')
  const panel = page.getByRole('region', { name: 'Novel reference (English translation)' })
  await expect(panel.getByText('none saved', { exact: true })).toBeVisible()
  await panel.locator('.section-title').click()
  await expect(page.getByTestId('novel-file-status-reference')).toHaveText('Nothing saved yet.')

  const save = panel.getByRole('button', { name: 'Save', exact: true })
  await expect(save).toBeDisabled()
  // A type the server refuses is caught before any request.
  await panel.getByLabel('Reference file', { exact: true }).setInputFiles(txt('novel.epub'))
  await expect(panel.getByRole('alert')).toContainText('novel.epub')
  await expect(save).toBeDisabled()

  await panel.getByLabel('Reference file', { exact: true }).setInputFiles(txt())
  await save.click()
  await expect(panel.getByRole('status')).toHaveText(`Saved: ${(1500).toLocaleString()} characters.`)
  await expect(page.getByTestId('novel-file-status-reference')).toContainText('Saved:')
  expect(uploads).toHaveLength(1)
  expect(uploads[0].local).toBe('1')
  expect(uploads[0].body).toContain('name="file"')

  await panel.getByLabel('Reference file', { exact: true }).setInputFiles(txt('v2.md'))
  await panel.getByRole('button', { name: 'Replace saved text' }).click()
  await expect(panel.getByRole('status')).toContainText('Replaced')

  await panel.getByRole('button', { name: /Remove/ }).click()
  await panel.getByRole('button', { name: 'Confirm remove novel reference' }).click()
  await expect(page.getByTestId('novel-file-status-reference')).toHaveText('Nothing saved yet.')
})

test('Transcribe: uploading the raw novel refreshes the automatic prompt', async ({ page }) => {
  const { uploads } = await mockNovelFiles(page)
  let uploaded = false
  await page.route('**/api/transcribe/dramas/1/config', async (route) => {
    const resp = await route.fetch()
    const json = await resp.json()
    return route.fulfill({ response: resp, json: uploaded ? { ...json, auto_initial_prompt: '云隐宗、沈清疑' } : json })
  })
  await page.goto('/#/drama/1/source')
  const panel = page.getByRole('region', { name: 'Raw novel (original language)' })
  await panel.locator('.section-title').click()
  await panel.getByLabel('Raw novel file', { exact: true }).setInputFiles({
    name: 'raw.epub', mimeType: 'application/epub+zip', buffer: Buffer.from('PK'),
  })
  uploaded = true
  await panel.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByTestId('novel-file-status-raw')).toContainText('Saved:')
  expect(uploads.map((u) => u.kind)).toEqual(['raw-novel'])
  const transcribe = page.getByRole('region', { name: 'Transcribe' })
  await transcribe.locator('.section-title', { hasText: 'Advanced' }).click()
  await expect(page.getByTestId('auto-prompt')).toContainText('云隐宗、沈清疑')
})

test('a 409 while a job runs is shown and nothing changes', async ({ page }) => {
  await mockNovelFiles(page, { uploadStatus: 409 })
  await page.goto('/#/drama/1/translate')
  const panel = page.getByRole('region', { name: 'Novel reference (English translation)' })
  await panel.locator('.section-title').click()
  await panel.getByLabel('Reference file', { exact: true }).setInputFiles(txt())
  await panel.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(panel.getByRole('alert')).toBeVisible()
  await expect(page.getByTestId('novel-file-status-reference')).toHaveText('Nothing saved yet.')
})

test('remote viewers see the status but no upload or remove controls', async ({ page }) => {
  const { store } = await mockNovelFiles(page)
  store.reference = { drama_id: 1, present: true, size_bytes: 100, char_count: 90 }
  await page.route('**/api/meta', async (route) => {
    const resp = await route.fetch()
    return route.fulfill({ response: resp, json: { ...(await resp.json()), local: false } })
  })
  await page.goto('/#/drama/1/translate')
  const panel = page.getByRole('region', { name: 'Novel reference (English translation)' })
  await panel.locator('.section-title').click()
  await expect(page.getByTestId('novel-file-status-reference')).toContainText('90 characters')
  await expect(panel.getByText('Run this on the main PC.')).toBeVisible()
  await expect(panel.getByLabel('Reference file', { exact: true })).toHaveCount(0)
  await expect(panel.getByRole('button', { name: /Remove/ })).toHaveCount(0)
})

test('paste text instead of a file, with a live character count', async ({ page }) => {
  const { pastes, uploads } = await mockNovelFiles(page)
  await page.goto('/#/drama/1/translate')
  const panel = page.getByRole('region', { name: 'Novel reference (English translation)' })
  await panel.locator('.section-title').click()
  await panel.getByRole('radio', { name: 'Paste text' }).check()
  await expect(panel.getByLabel('Reference file', { exact: true })).toHaveCount(0)
  const save = panel.getByRole('button', { name: 'Save', exact: true })
  await panel.getByLabel('Reference text', { exact: true }).fill('   ')
  await expect(save).toBeDisabled()
  await panel.getByLabel('Reference text', { exact: true }).fill('Shen Qingyi drew her sword.')
  await expect(page.getByTestId('novel-file-count-reference')).toHaveText('27 characters')
  await save.click()
  await expect(panel.getByRole('status')).toHaveText('Saved: 27 characters.')
  await expect(page.getByTestId('novel-file-status-reference')).toContainText('Saved: 27 characters')
  await expect(panel.getByLabel('Reference text', { exact: true })).toHaveValue('')
  expect(uploads).toEqual([])
  expect(pastes).toEqual([{ kind: 'reference', local: '1', json: { text: 'Shen Qingyi drew her sword.' } }])
})

test('raw novel saved in Transcribe shows the glossary link in Novel text', async ({ page }) => {
  await mockNovelFiles(page)
  await page.goto('/#/drama/1/source')
  await page.locator('.section-title', { hasText: /^Novel text$/ }).click()
  const link = page.getByRole('link', { name: 'Build a glossary from this novel (Translate → Glossary) →' })
  await expect(page.getByTestId('novel-status')).toBeVisible()
  await expect(link).toHaveCount(0)
  const panel = page.getByRole('region', { name: 'Raw novel (original language)' })
  await panel.locator('.section-title').click()
  await panel.getByRole('radio', { name: 'Paste text' }).check()
  await panel.getByLabel('Raw novel text', { exact: true }).fill('云隐宗')
  await panel.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(link).toBeVisible()
  await panel.getByRole('button', { name: /Remove/ }).click()
  await panel.getByRole('button', { name: 'Confirm remove raw novel' }).click()
  await expect(page.getByTestId('novel-file-status-raw')).toHaveText('Nothing saved yet.')
  await expect(link).toHaveCount(0)
})
