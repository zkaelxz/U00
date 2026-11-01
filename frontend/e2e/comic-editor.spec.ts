import { expect, test, type Page } from '@playwright/test'

import { mockEditor } from './comicEditorMocks'

// Comic text-box editor (#/comic/7/edit) at the desktop viewport, against the
// stubbed page detail / PATCH / undo / render routes (comicEditorMocks.ts).

const box = (page: Page, id: number) => page.getByTestId(`ce-box-${id}`)

async function open(page: Page) {
  const stub = await mockEditor(page)
  await page.goto('/#/comic/7/edit?page=1')
  await expect(box(page, 11)).toBeVisible()
  return stub
}

// Mouse path from the centre of a locator, by (dx, dy) screen pixels.
async function dragBy(page: Page, target: ReturnType<Page['getByTestId']>, dx: number, dy: number) {
  const b = (await target.boundingBox())!
  const x = b.x + b.width / 2
  const y = b.y + b.height / 2
  await page.mouse.move(x, y)
  await page.mouse.down()
  await page.mouse.move(x + dx / 2, y + dy / 2, { steps: 4 })
  await page.mouse.move(x + dx, y + dy, { steps: 4 })
  await page.mouse.up()
}

test('Apply sends one PATCH with the page rev, and Undo goes back', async ({ page }) => {
  const s = await open(page)
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Page 1 of 4')
  await box(page, 12).click()
  await expect(page.getByLabel('Original')).toHaveValue('那你身后站着的是谁？')
  await expect(page.getByLabel('Original')).toHaveJSProperty('readOnly', true)
  await page.getByLabel('English').fill('Then who is behind you?')
  await page.getByLabel('Size').fill('24')
  await page.getByRole('button', { name: '#b3261e' }).click()
  expect(s.patches).toHaveLength(0)
  await page.getByRole('button', { name: 'Apply' }).click()
  await expect(page.getByRole('button', { name: 'Undo' })).toBeEnabled()
  expect(s.patches).toHaveLength(1)
  expect(s.patches[0].body).toEqual({
    expected_rev: 1,
    expected: { translated_text: 'Then who is standing behind you?', font_size: 18, text_color: null },
    changes: { translated_text: 'Then who is behind you?', font_size: 24, text_color: '#b3261e' },
  })
  await expect(page.getByLabel('English')).toHaveValue('Then who is behind you?')

  await page.getByRole('button', { name: 'Undo' }).click()
  await expect(page.getByLabel('English')).toHaveValue('Then who is standing behind you?')
  expect(s.undos).toEqual([{ expected_rev: 2 }])
  await expect(page.getByRole('button', { name: 'Undo' })).toBeDisabled()
  expect(s.comic.unmocked).toEqual([])
})

test('a drag and a corner resize each send one PATCH', async ({ page }) => {
  const s = await open(page)
  await dragBy(page, box(page, 11), 48, 24)
  await expect.poll(() => s.patches.length).toBe(1)
  const moved = s.patches[0].body
  expect(moved.expected_rev).toBe(1)
  expect(Object.keys(moved.changes).sort()).toEqual(['x', 'y'])
  // 48 and 24 screen px at about 0.48 scale are about 100 and 50 page px.
  expect(Number(moved.changes.x)).toBeGreaterThan(150)
  expect(Number(moved.changes.x)).toBeLessThan(250)

  await expect(box(page, 11).getByTestId('ce-handle-se')).toBeVisible()
  const handle = box(page, 11).getByTestId('ce-handle-se')
  await dragBy(page, handle, 24, 12)
  await expect.poll(() => s.patches.length).toBe(2)
  const resized = s.patches[1].body
  expect(resized.expected_rev).toBe(2)
  expect(Object.keys(resized.changes).sort()).toEqual(['h', 'w'])
  expect(Number(resized.changes.w)).toBeGreaterThan(300)
  expect(s.comic.unmocked).toEqual([])
})

test('a box dragged past the page edge stays inside it', async ({ page }) => {
  const s = await open(page)
  await dragBy(page, box(page, 11), -2000, -2000)
  await expect.poll(() => s.patches.length).toBe(1)
  expect(s.patches[0].body.changes).toEqual({ x: 0, y: 0 })
})

test('a click without moving sends nothing', async ({ page }) => {
  const s = await open(page)
  await box(page, 11).click()
  await expect(page.getByLabel('English')).toHaveValue('I told you not to open that door.')
  expect(s.patches).toHaveLength(0)
})

test('a refused write shows the error and reloads the page', async ({ page }) => {
  const s = await open(page)
  await box(page, 11).click()
  const before = s.pageGets
  s.conflictNext = true
  await page.getByLabel('English').fill('Changed');
  await page.getByRole('button', { name: 'Apply' }).click()
  await expect(page.getByRole('alert')).toBeVisible()
  await expect.poll(() => s.pageGets).toBeGreaterThan(before)
  // The reloaded rev (2) is what the next write carries.
  await page.getByLabel('English').fill('Changed again')
  await page.getByRole('button', { name: 'Apply' }).click()
  await expect(page.getByRole('alert')).toHaveCount(0)
  expect(s.patches.map((p) => p.body.expected_rev)).toEqual([1, 2])
})

test('Show Original swaps the picture; Save page starts the render', async ({ page }) => {
  const s = await open(page)
  const img = page.getByTestId('ce-stage').locator('img')
  await expect(img).toHaveAttribute('src', /variant=rendered/)
  await page.getByRole('button', { name: 'Show Original' }).click()
  await expect(img).toHaveAttribute('src', /variant=original/)
  await page.getByRole('button', { name: 'Show Original' }).click()
  await expect(img).toHaveAttribute('src', /variant=rendered/)
  await page.getByRole('button', { name: 'Save page' }).click()
  await expect.poll(() => s.renders.length).toBe(1)
  expect(s.renders[0]).toEqual({ page_id: 701 })
})

test('Previous and Next move between pages', async ({ page }) => {
  await open(page)
  await expect(page.getByRole('button', { name: 'Previous' })).toBeDisabled()
  await page.getByRole('button', { name: 'Next' }).click()
  await expect(page).toHaveURL(/#\/comic\/7\/edit\?page=2$/)
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Page 2 of 4')
})

test('disabled editor buttons stay readable in dark mode', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' })
  await open(page)
  const undo = page.getByRole('button', { name: 'Undo' })
  await expect(undo).toBeDisabled()
  // Colours are normalised through a canvas because color-mix() computes to color(srgb ...). The page
  // background is the --bg token the frosted wallpaper sits on.
  const [fg, bg] = await undo.evaluate((el) => {
    const ctx = document.createElement('canvas').getContext('2d')!
    const rgb = (c: string) => {
      ctx.clearRect(0, 0, 1, 1)
      ctx.fillStyle = c
      ctx.fillRect(0, 0, 1, 1)
      return Array.from(ctx.getImageData(0, 0, 1, 1).data.slice(0, 3))
    }
    return [rgb(getComputedStyle(el).color), rgb(getComputedStyle(document.documentElement).getPropertyValue('--bg'))]
  })
  const lum = (c: number[]) => {
    const [r, g, b] = c.map((v) => ((v /= 255) <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b
  }
  const [hi, lo] = [Math.max(lum(fg), lum(bg)), Math.min(lum(fg), lum(bg))]
  expect((hi + 0.05) / (lo + 0.05)).toBeGreaterThanOrEqual(3)
})

// A mouse drag between two page points (800 x 1200 page), which may lie outside the stage.
async function drawBetween(page: Page, from: [number, number], to: [number, number]) {
  const b = (await page.getByTestId('ce-stage').boundingBox())!
  const at = ([px, py]: [number, number]) => [b.x + (px / 800) * b.width, b.y + (py / 1200) * b.height] as const
  const [x1, y1] = at(from)
  const [x2, y2] = at(to)
  await page.mouse.move(x1, y1)
  await page.mouse.down()
  await page.mouse.move((x1 + x2) / 2, (y1 + y2) / 2, { steps: 4 })
  await page.mouse.move(x2, y2, { steps: 4 })
  await page.mouse.up()
}

test('Delete box sends one DELETE with the page rev, selects nothing, and Undo brings the box back', async ({ page }) => {
  const s = await open(page)
  await box(page, 12).click()
  await page.getByRole('button', { name: 'Delete box' }).click()
  await expect(box(page, 12)).toHaveCount(0)
  await expect(page.getByLabel('English')).toHaveCount(0)
  expect(s.posts).toEqual([{ method: 'DELETE', rev: 1 }])
  await page.getByRole('button', { name: 'Undo' }).click()
  await expect(box(page, 12)).toBeVisible()
  expect(s.undos).toEqual([{ expected_rev: 2 }])
  expect(s.comic.unmocked).toEqual([])
})

test('a refused Delete box shows the error and reloads the page', async ({ page }) => {
  const s = await open(page)
  await box(page, 12).click()
  const before = s.pageGets
  s.conflictNext = true
  await page.getByRole('button', { name: 'Delete box' }).click()
  await expect(page.getByRole('alert')).toBeVisible()
  await expect.poll(() => s.pageGets).toBeGreaterThan(before)
  await expect(box(page, 12)).toBeVisible()
  await box(page, 12).click()
  await page.getByRole('button', { name: 'Delete box' }).click()
  await expect(box(page, 12)).toHaveCount(0)
  expect(s.posts.map((p) => p.rev)).toEqual([1, 2])
})

test('Draw sends one POST with the rectangle, selects the new box and returns to Select', async ({ page }) => {
  const s = await open(page)
  await page.getByRole('button', { name: 'Draw' }).click()
  await expect(page.getByRole('button', { name: 'Draw' })).toHaveAttribute('aria-pressed', 'true')
  await drawBetween(page, [200, 800], [500, 900])
  await expect(page.getByLabel('English')).toBeVisible()
  expect(s.posts).toHaveLength(1)
  expect(s.posts[0].method).toBe('POST')
  expect(s.posts[0].rev).toBe(1)
  const r = s.posts[0].region!
  expect(Math.abs(r.x - 200)).toBeLessThan(8)
  expect(Math.abs(r.y - 800)).toBeLessThan(8)
  expect(Math.abs(r.w - 300)).toBeLessThan(10)
  expect(Math.abs(r.h - 100)).toBeLessThan(10)
  await expect(page.getByRole('button', { name: 'Select' })).toHaveAttribute('aria-pressed', 'true')
  await expect(box(page, 32)).toHaveAttribute('data-selected', 'true')
  expect(s.patches).toHaveLength(0)

  await page.getByRole('button', { name: 'Undo' }).click()
  await expect(box(page, 32)).toHaveCount(0)
  expect(s.undos).toEqual([{ expected_rev: 2 }])
})

test('a reversed drag that leaves the page is clamped to it', async ({ page }) => {
  const s = await open(page)
  await page.getByRole('button', { name: 'Draw' }).click()
  await drawBetween(page, [100, 1100], [-200, 900])
  await expect.poll(() => s.posts.length).toBe(1)
  const r = s.posts[0].region!
  expect(r.x).toBe(0)
  expect(Math.abs(r.w - 100)).toBeLessThan(8)
  expect(Math.abs(r.y - 900)).toBeLessThan(8)
  expect(Math.abs(r.h - 200)).toBeLessThan(10)
})

test('a tiny drag sends nothing and Draw stays active', async ({ page }) => {
  const s = await open(page)
  await page.getByRole('button', { name: 'Draw' }).click()
  await drawBetween(page, [200, 800], [203, 802])
  await page.getByTestId('ce-stage').click({ position: { x: 100, y: 500 } })
  expect(s.posts).toEqual([])
  await expect(page.getByRole('button', { name: 'Draw' })).toHaveAttribute('aria-pressed', 'true')
})

test('a refused Draw shows the error and reloads the page', async ({ page }) => {
  const s = await open(page)
  const before = s.pageGets
  s.conflictNext = true
  await page.getByRole('button', { name: 'Draw' }).click()
  await drawBetween(page, [200, 800], [500, 900])
  await expect(page.getByRole('alert')).toBeVisible()
  await expect.poll(() => s.pageGets).toBeGreaterThan(before)
  await page.getByRole('button', { name: 'Draw' }).click()
  await drawBetween(page, [200, 800], [500, 900])
  await expect(page.getByRole('alert')).toHaveCount(0)
  expect(s.posts.map((p) => p.rev)).toEqual([1, 2])
})

test('a Kind change is one PATCH with the page rev, and Undo puts the kind back', async ({ page }) => {
  const s = await open(page)
  await box(page, 11).click()
  await expect(page.getByLabel('Kind')).toHaveValue('bubble')
  await page.getByLabel('Kind').selectOption('narration')
  expect(s.patches).toHaveLength(0)
  await page.getByRole('button', { name: 'Apply' }).click()
  await expect.poll(() => s.patches.length).toBe(1)
  expect(s.patches[0].body).toEqual({ expected_rev: 1, expected: { kind: 'bubble' }, changes: { kind: 'narration' } })
  await expect(page.getByLabel('Kind')).toHaveValue('narration')
  await page.getByRole('button', { name: 'Undo' }).click()
  await expect(page.getByLabel('Kind')).toHaveValue('bubble')
  expect(s.comic.unmocked).toEqual([])
})

test('a drawn box starts as a bubble and takes the Kind chosen after it', async ({ page }) => {
  const s = await open(page)
  await page.getByRole('button', { name: 'Draw' }).click()
  await drawBetween(page, [200, 800], [500, 900])
  await expect(box(page, 32)).toHaveAttribute('data-selected', 'true')
  await expect(page.getByLabel('Kind')).toHaveValue('bubble')
  await page.getByLabel('Kind').selectOption('sfx')
  await page.getByRole('button', { name: 'Apply' }).click()
  await expect.poll(() => s.patches.length).toBe(1)
  expect(s.patches[0].path).toMatch(/\/regions\/32$/)
  expect(s.patches[0].body).toEqual({ expected_rev: 2, expected: { kind: 'bubble' }, changes: { kind: 'sfx' } })
  expect(s.posts.map((p) => p.method)).toEqual(['POST'])
})
