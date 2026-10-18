import { readFileSync } from 'node:fs'

import { expect, test, type Page } from '@playwright/test'

// A 1x1 transparent PNG: the preview needs a real picture, the read itself is mocked.
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
  'base64',
)

const ENGINES = {
  default_engine: 'ollama',
  items: [
    { name: 'deepseek', label: 'Paid, cloud', free: false, models: null, key_configured: true },
    { name: 'claude', label: 'Paid, cloud', free: false, models: null, key_configured: true },
    { name: 'ollama', label: 'Free and private', free: true, models: ['gemma4:12b'], key_configured: true },
  ],
}

async function mock(page: Page, reads: string[]) {
  await page.route('**/api/translate/engines', (r) => r.fulfill({ json: ENGINES }))
  await page.route('**/api/translate/read-image/estimate', (r) =>
    r.fulfill({ json: { engine: 'claude', model: null, estimated_usd: 0.0047, cap_applies: true, monthly_cap_usd: 5, cap_left_usd: 4.5, fits_cap: true } }),
  )
  await page.route('**/api/translate/read-image', (r) => {
    reads.push(r.request().postData() ?? '')
    return r.fulfill({ json: { text: '你好，世界' } })
  })
}

test('reads a chosen picture into the text box and appends the next one', async ({ page }) => {
  const reads: string[] = []
  await mock(page, reads)
  await page.goto('/#/translate')
  await page.getByRole('button', { name: 'Read from a screenshot…' }).click()
  const panel = page.getByRole('group', { name: 'Read from a screenshot' })

  // DeepSeek takes no pictures, so it is not offered.
  await expect(panel.getByLabel('Read with', { exact: true }).locator('option')).toHaveText(['Claude', 'Ollama (local)'])
  await expect(panel.getByRole('button', { name: 'Read the text' })).toBeDisabled()

  await panel.locator('input[type=file]').setInputFiles({ name: 'frame.png', mimeType: 'image/png', buffer: PNG })
  await expect(panel.getByAltText('The picture to read')).toBeVisible()
  await panel.getByRole('button', { name: 'Read the text' }).click()
  await expect(page.getByLabel('Text to translate')).toHaveValue('你好，世界')
  expect(reads[0]).toContain('name="engine"')
  expect(reads[0]).toContain('ollama')

  await panel.getByRole('button', { name: 'Read the text' }).click()
  await expect(page.getByLabel('Text to translate')).toHaveValue('你好，世界\n你好，世界')
})

test('a paid engine shows its price before the read', async ({ page }) => {
  await mock(page, [])
  await page.goto('/#/translate')
  await page.getByRole('button', { name: 'Read from a screenshot…' }).click()
  const panel = page.getByRole('group', { name: 'Read from a screenshot' })
  await panel.locator('input[type=file]').setInputFiles({ name: 'frame.png', mimeType: 'image/png', buffer: PNG })
  await panel.getByLabel('Read with', { exact: true }).selectOption('claude')
  await expect(panel.getByTestId('read-estimate')).toContainText('About $0.0047; cap left $4.50')
})

test('something that is not a picture is refused in plain words', async ({ page }) => {
  await mock(page, [])
  await page.goto('/#/translate')
  await page.getByRole('button', { name: 'Read from a screenshot…' }).click()
  const panel = page.getByRole('group', { name: 'Read from a screenshot' })
  await panel.locator('input[type=file]').setInputFiles({ name: 'a.gif', mimeType: 'image/gif', buffer: PNG })
  await expect(panel.getByText('not a PNG, JPEG or WebP picture')).toBeVisible()
})

// READ_SHOTS_DIR=<dir> (and READ_PIC=<png>) saves a screenshot per theme for the owner's boards.
const SHOTS = process.env.READ_SHOTS_DIR
const PICTURE = process.env.READ_PIC ? readFileSync(process.env.READ_PIC) : PNG

for (const [theme, size] of [['light', 'desktop'], ['dark', 'desktop'], ['sepia', 'desktop'], ['oled', 'desktop'], ['light', 'phone'], ['dark', 'phone']] as const) {
  test(`the screenshot box fits and a box can be dragged (${theme}, ${size})`, async ({ page }) => {
    await page.addInitScript((t) => localStorage.setItem('baihe.theme', t), theme)
    await page.setViewportSize(size === 'phone' ? { width: 390, height: 844 } : { width: 1280, height: 900 })
    await mock(page, [])
    await page.goto('/#/translate')
    await page.getByRole('button', { name: 'Read from a screenshot…' }).click()
    const panel = page.getByRole('group', { name: 'Read from a screenshot' })
    await panel.locator('input[type=file]').setInputFiles({ name: 'frame.png', mimeType: 'image/png', buffer: PICTURE })
    const image = panel.getByAltText('The picture to read')
    await expect(image).toBeVisible()
    await image.evaluate((el) => el.scrollIntoView({ block: 'center' }))
    const at = (await image.boundingBox())!
    await page.mouse.move(at.x + at.width * 0.1, at.y + at.height * 0.7)
    await page.mouse.down()
    await page.mouse.move(at.x + at.width * 0.9, at.y + at.height * 0.86, { steps: 4 })
    await page.mouse.up()
    await expect(panel.locator('.translate-read-box')).toBeVisible()
    await expect(panel.getByText(/Sends \d+×\d+ px/)).toBeVisible()
    await panel.getByLabel('Read with', { exact: true }).selectOption('claude')
    await expect(panel.getByTestId('read-estimate')).toBeVisible()
    const sideways = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    expect(sideways).toBeLessThanOrEqual(0)
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/read-screenshot-${theme}-${size}.png`, fullPage: true })
  })
}
