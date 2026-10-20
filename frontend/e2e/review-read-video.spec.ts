import { expect, test } from '@playwright/test'

import { seedVideo } from './playerMocks'

const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='

// "Read from video" under the side video: a frame at the active line's start
// goes through Quick translate's read route and lands in that line's Source
// box as an unsaved edit. The read itself is mocked.

const ENGINES = {
  default_engine: 'ollama',
  items: [{ name: 'ollama', label: 'Free and private', free: true, models: ['gemma4:12b'], key_configured: true }],
}

test.beforeEach(async ({ page }) => {
  test.skip(!seedVideo(), 'OpenCV is not installed, so there is no test video')
  await page.route('**/api/translate/engines', (r) => r.fulfill({ json: ENGINES }))
  await page.route('**/api/translate/read-image', (r) => r.fulfill({ json: { text: '你真的以为我会放过你吗' } }))
  await page.goto('/#/drama/3/review')
  await expect(page.locator('video.review-video')).toBeVisible()
})

test('reads the frame at a line start into that line, unsaved until saved', async ({ page }) => {
  const rows = page.locator('.review-line:not(.review-skeleton)')
  await rows.nth(1).click()
  await page.getByRole('button', { name: 'Read from video' }).click()
  await page.getByRole('button', { name: 'Use frame at line start' }).click()

  const panel = page.getByRole('group', { name: 'Read from video' })
  await expect(panel.getByAltText('The picture to read')).toBeVisible()
  expect(await page.locator('video.review-video').evaluate((v) => (v as HTMLVideoElement).currentTime)).toBeCloseTo(3, 0)

  await panel.getByRole('button', { name: 'Read into this line' }).click()
  await expect(rows.nth(1).getByLabel('Source')).toHaveValue('你真的以为我会放过你吗')
  await expect(panel.getByText("Put into the line's source text")).toBeVisible()

  await rows.nth(1).getByLabel('Source').press('Escape')
  await rows.nth(1).getByLabel('Source').press('Escape')
  await expect(rows.nth(1)).not.toContainText('你真的以为我会放过你吗')
})

test('Read & translate puts the read in Source and its translation in English, both unsaved', async ({ page }) => {
  let sent: Record<string, string> = {}
  await page.route('**/api/translate', (r) => {
    sent = r.request().postDataJSON()
    return r.fulfill({ json: { translated_text: 'Do you really think I will let you go?' } })
  })
  const rows = page.locator('.review-line:not(.review-skeleton)')
  await rows.nth(1).click()
  await page.getByRole('button', { name: 'Read from video' }).click()
  await page.getByRole('button', { name: 'Use frame at line start' }).click()
  const panel = page.getByRole('group', { name: 'Read from video' })
  await expect(panel.getByAltText('The picture to read')).toBeVisible()

  await panel.getByRole('button', { name: 'Read & translate' }).click()
  await expect(rows.nth(1).getByLabel('Source')).toHaveValue('你真的以为我会放过你吗')
  await expect(rows.nth(1).getByLabel('Translation')).toHaveValue('Do you really think I will let you go?')
  expect(sent).toMatchObject({ text: '你真的以为我会放过你吗', target_language: 'en' })
})

test('is closed until opened, and stays as the viewer left it', async ({ page }) => {
  const panel = page.getByRole('group', { name: 'Read from video' })
  await expect(page.getByRole('button', { name: 'Read from video' })).toBeVisible()
  await expect(panel).toHaveCount(0)

  await page.getByRole('button', { name: 'Read from video' }).click()
  await expect(panel).toBeVisible()
  await page.reload()
  await expect(panel).toBeVisible()

  await panel.getByRole('button', { name: 'Close' }).click()
  await expect(panel).toHaveCount(0)
  await page.reload()
  await expect(page.getByRole('button', { name: 'Read from video' })).toBeVisible()
  await expect(panel).toHaveCount(0)
})

test('Ctrl+V of a picture opens the panel with it while a line is selected', async ({ page }) => {
  const rows = page.locator('.review-line:not(.review-skeleton)')
  const panel = page.getByRole('group', { name: 'Read from video' })
  const paste = (withImage: boolean) =>
    page.evaluate(({ withImage, png }) => {
      const dt = new DataTransfer()
      if (withImage) {
        const bin = atob(png)
        dt.items.add(new File([Uint8Array.from(bin, (c) => c.charCodeAt(0))], 'shot.png', { type: 'image/png' }))
      } else dt.setData('text/plain', 'just text')
      document.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }))
    }, { withImage, png: PNG })

  await rows.nth(1).click()
  await paste(false)
  await expect(panel).toHaveCount(0)

  await paste(true)
  await expect(panel.getByAltText('The picture to read')).toBeVisible()
  await panel.getByRole('button', { name: 'Read into this line' }).click()
  await expect(rows.nth(1).getByLabel('Source')).toHaveValue('你真的以为我会放过你吗')
})
