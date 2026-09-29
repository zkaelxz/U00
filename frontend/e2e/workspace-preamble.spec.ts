import { expect, test } from '@playwright/test'

// Workspace preamble/Source parity (inventory P04, P13, P14, S12, S13).
// The drama reads, the platforms list and the cover upload hit the real
// seeded API (the cover goes on drama 3, which no other spec checks for one);
// the LLM romanize call and the novel attaches are mocked.

// A 2x3 red PNG.
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAIAAAADCAIAAAA2iEnWAAAAFElEQVR4nGM8wcXFwMDAxMDAgKAAEWYA4jBK+akAAAAASUVORK5CYII=',
  'base64',
)

test('romanize credits sends one request and shows the credits bilingually', async ({ page }) => {
  const bodies: unknown[] = []
  await page.route('**/api/metadata/dramas/1/romanize-credits', (r) => {
    bodies.push(r.request().postDataJSON())
    return r.fulfill({ json: { drama_id: 1, romanized: { author: 'Mo Xiang Tong Xiu' }, updated: true } })
  })
  await page.goto('/#/drama/1/source')
  await page.locator('.section-title', { hasText: 'Credits & cover' }).click()
  await expect(page.getByTestId('credits')).toContainText('Mo Xiang Tong Xiu (墨香铜臭)')
  await page.getByRole('button', { name: 'Romanize credits' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Credits romanized' })).toBeVisible()
  expect(bodies).toEqual([{}])
})

test('cover upload checks the type, then saves and shows the cover', async ({ page }) => {
  await page.goto('/#/drama/3/source')
  await page.locator('.section-title', { hasText: 'Credits & cover' }).click()
  await expect(page.getByText('No cover.')).toBeVisible()
  const input = page.getByLabel('Cover image', { exact: true })
  await input.setInputFiles({ name: 'x.gif', mimeType: 'image/gif', buffer: Buffer.from('GIF89a') })
  await expect(page.getByRole('alert')).toHaveText('Choose a PNG, JPEG or WebP image.')
  await expect(page.getByRole('button', { name: 'Upload cover' })).toBeDisabled()
  await input.setInputFiles({ name: 'cover.png', mimeType: 'image/png', buffer: PNG })
  await page.getByRole('button', { name: 'Upload cover' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Cover saved (2×3).' })).toBeVisible()
  const img = page.getByRole('img', { name: 'Cover of Signal' })
  await expect(img).toBeVisible()
  expect(await img.evaluate((el) => (el as HTMLImageElement).naturalWidth)).toBe(2)
  await expect(page.getByRole('button', { name: 'Replace cover' })).toBeVisible()
})

test('auto-fill lists the known official platforms', async ({ page }) => {
  await page.goto('/#/drama/1/source')
  await page.locator('.section-title', { hasText: 'Auto-fill metadata' }).click()
  await page.getByText('Known official platforms', { exact: true }).click()
  const list = page.getByRole('list', { name: 'Known official platforms' })
  const link = list.getByRole('link', { name: 'JJWXC (晋江文学城)' })
  await expect(link).toHaveAttribute('href', 'https://www.jjwxc.net')
  await expect(link).toHaveAttribute('rel', 'noopener noreferrer')
})

test('EPUB chapter range and chapters from Sources', async ({ page }) => {
  await page.route('**/api/novel/dramas/2/raw-novel', (r) =>
    r.fulfill({ json: { drama_id: 2, present: true, size_bytes: 30, char_count: 10 } }),
  )
  const forms: string[] = []
  await page.route('**/api/novel/dramas/2/attach-epub', (r) => {
    forms.push(r.request().postData() ?? '')
    return r.fulfill({ json: { char_count: 1234, epub_chapters: 40, chapter_from: 2, chapter_to: 5 } })
  })
  const fromSources: unknown[] = []
  await page.route('**/api/novel/dramas/2/attach-from-sources', (r) => {
    fromSources.push(r.request().postDataJSON())
    return r.fulfill({ json: { char_count: 10 } })
  })
  await page.goto('/#/drama/2/source')
  const novel = page.getByRole('region', { name: 'Novel text' })
  if (!(await novel.getByLabel('EPUB file', { exact: true }).isVisible())) await novel.locator('.section-title', { hasText: 'Novel text' }).click()
  await novel.getByLabel('EPUB file', { exact: true }).setInputFiles({ name: 'b.epub', mimeType: 'application/epub+zip', buffer: Buffer.from('PK') })
  await novel.getByLabel('From chapter', { exact: true }).fill('6')
  await novel.getByLabel('To chapter', { exact: true }).fill('5')
  await expect(novel.getByRole('alert')).toHaveText('The first chapter comes after the last.')
  await expect(novel.getByRole('button', { name: 'Attach EPUB' })).toBeDisabled()
  await novel.getByLabel('From chapter', { exact: true }).fill('2')
  await novel.getByRole('button', { name: 'Attach EPUB' }).click()
  await expect(novel.getByRole('status')).toHaveText('Attached 1,234 characters (chapters 2–5 of 40).')
  expect(forms[0]).toContain('name="chapter_from"\r\n\r\n2')
  expect(forms[0]).toContain('name="chapter_to"\r\n\r\n5')

  await novel.getByRole('button', { name: 'Use chapters imported in Sources' }).click()
  await expect(novel.getByRole('status')).toHaveText('Attached 10 characters.')
  expect(fromSources).toEqual([{ mode: 'replace' }])
})
