import { expect, test } from './fixtures'

// The raw novel on the Source page against the real API: a Chinese novel saved as GB18030 (as many
// downloads are) or UTF-8 with a BOM is read as text, not refused or garbled. Removed after each test.

const TEXT = '第一章 云隐宗\n沈清疑走进山门。\n'

test.afterEach(async ({ request }) => {
  const status = await (await request.get('/api/novel/dramas/1/raw-novel')).json() as { present: boolean }
  if (!status.present) return
  const r = await request.post('/api/novel/dramas/1/raw-novel/remove', { headers: { 'X-Baihe-Local': '1' }, data: { confirm: true } })
  expect(r.ok()).toBe(true)
})

async function upload(page: import('@playwright/test').Page, buffer: Buffer) {
  await page.goto('/#/drama/1/source')
  await page.locator('.section-title', { hasText: /^Attach novel text \(optional\)$/ }).click()
  const panel = page.getByRole('region', { name: 'Raw source novel (original language, used as reference)' })
  await panel.locator('.section-title').click()
  await panel.getByLabel('Raw novel file', { exact: true }).setInputFiles({ name: 'raw.txt', mimeType: 'text/plain', buffer })
  const saved = page.waitForResponse((r) => r.url().endsWith('/api/novel/dramas/1/raw-novel') && r.request().method() === 'POST')
  await panel.getByRole('button', { name: 'Save', exact: true }).click()
  return { panel, response: await saved }
}

// TEXT as GB18030 bytes (Node has no GB18030 encoder).
const GB18030 = Buffer.from('b5dad2bbd5c220d4c6d2fed7da0ac9f2c7e5d2c9d7dfbdf8c9bdc3c5a1a30a', 'hex')
const CHARS = [...TEXT.trim()].length

test('a GB18030 raw novel is read as Chinese, with a note to check it', async ({ page }) => {
  const { panel, response } = await upload(page, GB18030)
  expect(response.status()).toBe(200)
  await expect(panel.getByRole('status')).toContainText(`Saved: ${CHARS} characters.`)
  await expect(panel.getByRole('status')).toContainText("The file isn't UTF-8")
  await expect(page.getByTestId('novel-file-status-raw')).toContainText(`${CHARS} characters`)
})

test('a UTF-8 raw novel with a byte-order mark is saved without a note and without the mark', async ({ page }) => {
  const { panel, response } = await upload(page, Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), Buffer.from(TEXT, 'utf8')]))
  expect(response.status()).toBe(200)
  await expect(panel.getByRole('status')).toHaveText(`Saved: ${CHARS} characters.`)
})

test('an empty file is refused with the server\'s reason and nothing is saved', async ({ page }) => {
  const { panel, response } = await upload(page, Buffer.from('\n\n  \n'))
  expect(response.status()).toBe(422)
  await expect(panel.getByRole('alert')).toContainText('No readable text was found in that file.')
  const status = await (await page.request.get('/api/novel/dramas/1/raw-novel')).json() as { present: boolean }
  expect(status.present).toBe(false)
})
