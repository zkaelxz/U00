import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test } from '@playwright/test'

import { currentTime, FOUR_LINES, seedVideo, seekVideo, stubFullscreen } from './playerMocks'

// The Review video: arrow-key seeking, subtitles drawn over the picture (never
// changing the layout) and full screen.

test.beforeEach(async ({ page }) => {
  test.skip(!seedVideo(), 'OpenCV is not installed, so there is no test video')
  await stubFullscreen(page)
  await page.goto('/#/drama/3/review')
  await expect(page.locator('video.review-video')).toBeVisible()
  await expect.poll(() => page.locator('video.review-video').evaluate((v) => (v as HTMLVideoElement).readyState)).toBeGreaterThanOrEqual(1)
})

const frame = (page: import('@playwright/test').Page) => page.getByTestId('player-frame')
const box = (loc: import('@playwright/test').Locator) => loc.evaluate((el) => {
  const r = el.getBoundingClientRect()
  return { x: r.x, y: r.y, width: r.width, height: r.height }
})

test('Left and Right seek 5 s (Shift 1 s) only while the player has focus', async ({ page }) => {
  await seekVideo(page, 6)
  await frame(page).focus()
  await expect(frame(page)).toHaveAccessibleName(/Left and right arrows seek/)
  await page.keyboard.press('ArrowRight')
  await expect.poll(() => currentTime(page)).toBeCloseTo(11, 1)
  await page.keyboard.press('ArrowRight')
  await expect.poll(() => currentTime(page)).toBeCloseTo(12, 0) // clamped to the end
  await page.keyboard.press('Shift+ArrowLeft')
  await expect.poll(() => currentTime(page)).toBeLessThan(11.5)
  await seekVideo(page, 6)
  await page.keyboard.press('Shift+ArrowRight')
  await expect.poll(() => currentTime(page)).toBeCloseTo(7, 1)
  await seekVideo(page, 2)
  await page.keyboard.press('ArrowLeft')
  await expect.poll(() => currentTime(page)).toBe(0)

  // Typing in the Jump field keeps its own arrow keys.
  await seekVideo(page, 6)
  await page.getByLabel('Jump to time').fill('1')
  await page.keyboard.press('ArrowRight')
  expect(await currentTime(page)).toBeCloseTo(6, 1)
  // The seek bar uses the arrows itself (a small step), not the 5 s jump.
  await page.getByLabel('Seek', { exact: true }).focus()
  await page.keyboard.press('ArrowRight')
  expect(await currentTime(page)).toBeLessThan(7)
  // With focus on the line list nothing seeks either.
  await page.locator('body').click({ position: { x: 1, y: 1 } })
  await seekVideo(page, 6)
  await page.keyboard.press('ArrowRight')
  expect(await currentTime(page)).toBeCloseTo(6, 1)
})

test('Space plays and pauses while the player has focus', async ({ page }) => {
  await frame(page).focus()
  await page.keyboard.press('Space')
  await expect.poll(() => page.locator('video.review-video').evaluate((v) => !(v as HTMLVideoElement).paused)).toBe(true)
  await page.keyboard.press('Space')
  await expect.poll(() => page.locator('video.review-video').evaluate((v) => (v as HTMLVideoElement).paused)).toBe(true)
})

test('the subtitle is an overlay inside the picture and never changes the layout', async ({ page }) => {
  const overlay = page.getByTestId('player-overlay')
  const measure = async () => ({ video: await box(page.locator('video.review-video')), frame: await box(frame(page)), controls: await box(page.locator('.review-player-controls')), watch: await box(page.locator('.review-watch')) })

  await seekVideo(page, 1)
  await expect(overlay).toHaveText('Hello there')
  const one = await measure()
  const oneBox = await box(overlay)

  await seekVideo(page, 4)
  await expect(overlay).toHaveText(FOUR_LINES)
  const four = await measure()
  const fourBox = await box(overlay)
  expect(four).toEqual(one)
  expect(fourBox.height).toBeGreaterThan(oneBox.height * 2.5)
  // Sits inside the video's box, centred, at the bottom, and wraps upward.
  expect(fourBox.x).toBeGreaterThanOrEqual(four.video.x)
  expect(fourBox.x + fourBox.width).toBeLessThanOrEqual(four.video.x + four.video.width)
  expect(fourBox.y).toBeGreaterThanOrEqual(four.video.y)
  expect(fourBox.y + fourBox.height).toBeLessThanOrEqual(four.video.y + four.video.height)
  expect(fourBox.y + fourBox.height).toBeCloseTo(oneBox.y + oneBox.height, 0)
  expect(fourBox.width).toBeLessThanOrEqual(four.video.width * 0.9 + 1)
  expect(fourBox.x + fourBox.width / 2).toBeCloseTo(four.video.x + four.video.width / 2, 0)

  // No subtitle: no box, same layout.
  await seekVideo(page, 7)
  await expect(overlay).toHaveCount(0)
  expect(await measure()).toEqual(one)
  // Nothing is drawn below the picture.
  await expect(page.getByTestId('player-caption')).toHaveCount(0)
})

test('the language select still chooses the subtitle text, and Off removes it', async ({ page }) => {
  await seekVideo(page, 1)
  const overlay = page.getByTestId('player-overlay')
  await expect(overlay).toHaveText('Hello there')
  await page.getByRole('combobox', { name: /Subtitles/ }).selectOption('Source')
  await expect(overlay).toHaveText('你好')
  await page.getByRole('combobox', { name: /Subtitles/ }).selectOption('off')
  await expect(overlay).toHaveCount(0)
})

test('Full screen asks the wrapper (picture, subtitles and controls) and follows fullscreenchange', async ({ page }) => {
  const button = page.getByRole('button', { name: 'Full screen' })
  await expect(button).toHaveAttribute('aria-pressed', 'false')
  await seekVideo(page, 1)
  await button.click()
  await expect.poll(() => page.evaluate(() => (window as unknown as { __fsRequests: string[] }).__fsRequests)).toEqual(['player-frame'])
  const exit = page.getByRole('button', { name: 'Exit full screen' })
  await expect(exit).toHaveAttribute('aria-pressed', 'true')
  await expect(frame(page).locator('video.review-video')).toHaveCount(1)
  await expect(frame(page).getByTestId('player-overlay')).toHaveText('Hello there')
  await expect(frame(page).getByRole('slider', { name: 'Seek' })).toBeVisible()
  await expect(frame(page).getByRole('combobox', { name: /Speed/ })).toBeVisible()
  await expect(frame(page).getByRole('combobox', { name: /Subtitles/ })).toBeVisible()

  // Esc leaves natively: the browser fires fullscreenchange with no element.
  await page.evaluate(() => document.exitFullscreen())
  await expect(button).toHaveAttribute('aria-pressed', 'false')
})

test('full screen fills the screen when the video is pinned beside the lines', async ({ page }) => {
  // From 1400px wide the side video caps its stage height; full screen must override that.
  await page.setViewportSize({ width: 1600, height: 900 })
  await page.getByRole('button', { name: 'Full screen' }).click()
  await expect(page.getByRole('button', { name: 'Exit full screen' })).toBeVisible()
  await expect.poll(async () => (await box(page.locator('.review-stage'))).height).toBeGreaterThan(900 * 0.9)
})

test('with the video pinned beside the lines, Review opens at the top and the page ends with the lines section', async ({ page }) => {
  // Sixty lines with the first flagged one far down: the starting line used to be scrolled into view, moving the page,
  // and the bottom pager's screen-reader text escaped the lines scroller and stretched the page thousands of pixels.
  const libraryDir = path.join(path.resolve(process.cwd(), '..'), 'frontend', 'test-results', 'e2e-library')
  execFileSync(process.env.PYTHON ?? 'python', ['-c', `import db
db.configure_library_dir(${JSON.stringify(libraryDir)})
from core import Line
db.save_lines(3, [Line(idx=i, start=i * 0.2, end=i * 0.2 + 0.15, zh=f'第{i}句', en=f'Line {i}', flag='uncertain' if i == 30 else None) for i in range(60)])
`], { cwd: path.resolve(process.cwd(), '..') })
  await page.setViewportSize({ width: 1600, height: 900 })
  await page.reload()
  await expect(page.locator('.review-editor.has-side .review-line:not(.review-skeleton)')).toHaveCount(40)
  await expect(page.locator('.review-line[aria-current="true"]')).toContainText('#31')
  const scroll = () => page.evaluate(() => ({ y: window.scrollY, extra: document.documentElement.scrollHeight - window.innerHeight }))
  await expect.poll(async () => (await scroll()).extra).toBeLessThan(200)
  expect((await scroll()).y).toBe(0)
})

test('F and a double-click on the picture toggle full screen', async ({ page }) => {
  await frame(page).focus()
  await page.keyboard.press('f')
  await expect(page.getByRole('button', { name: 'Exit full screen' })).toBeVisible()
  await page.keyboard.press('f')
  await expect(page.getByRole('button', { name: 'Full screen' })).toBeVisible()
  await page.locator('video.review-video').dblclick()
  await expect(page.getByRole('button', { name: 'Exit full screen' })).toBeVisible()
})

test('in full screen the controls hide when idle and come back on movement', async ({ page }) => {
  await page.getByRole('button', { name: 'Full screen' }).click()
  const controls = page.locator('.review-player-controls')
  await expect(controls).toBeVisible()
  await page.getByRole('button', { name: 'Exit full screen' }).blur()
  await expect(frame(page)).toHaveClass(/is-idle/, { timeout: 8000 })
  await expect(controls).toBeHidden()
  const f = (await frame(page).boundingBox())!
  await page.mouse.move(f.x + 20, f.y + 20)
  await page.mouse.move(f.x + 60, f.y + 40)
  await expect(frame(page)).not.toHaveClass(/is-idle/)
  await expect(controls).toBeVisible()
})
