import { expect, test } from '@playwright/test'

import { mockEditor } from './comicEditorMocks'
import { expectNoSidewaysScroll } from './mockHelpers'

// Phone project: the comic editor stacks the page above the side panel.

test('select a box, edit it and Apply on a phone', async ({ page }) => {
  const s = await mockEditor(page)
  await page.goto('/#/comic/7/edit?page=1')
  await expect(page.getByTestId('ce-box-12')).toBeVisible()
  await expectNoSidewaysScroll(page)
  await page.getByTestId('ce-box-12').tap()
  const english = page.getByLabel('English')
  await expect(english).toBeVisible()
  await expectNoSidewaysScroll(page)
  await english.fill('Who is behind you?')
  await page.getByRole('button', { name: 'Apply' }).click()
  await expect.poll(() => s.patches.length).toBe(1)
  expect(s.patches[0].body.expected_rev).toBe(1)
  expect(s.patches[0].body.changes).toEqual({ translated_text: 'Who is behind you?' })
  await expectNoSidewaysScroll(page)
  expect(s.comic.unmocked).toEqual([])
})

test('choose a Kind and Apply on a phone', async ({ page }) => {
  const s = await mockEditor(page)
  await page.goto('/#/comic/7/edit?page=1')
  await page.getByTestId('ce-box-11').tap()
  const kind = page.getByLabel('Kind')
  await expect(kind).toBeVisible()
  await expectNoSidewaysScroll(page)
  await kind.selectOption('caption')
  await page.getByRole('button', { name: 'Apply' }).click()
  await expect.poll(() => s.patches.length).toBe(1)
  expect(s.patches[0].body).toEqual({ expected_rev: 1, expected: { kind: 'bubble' }, changes: { kind: 'caption' } })
  await expectNoSidewaysScroll(page)
  expect(s.comic.unmocked).toEqual([])
})

test('Save page has its own full-width row and Apply stays in view', async ({ page }) => {
  await mockEditor(page)
  await page.goto('/#/comic/7/edit?page=1')
  await expect(page.getByTestId('ce-box-12')).toBeVisible()
  const save = (await page.getByRole('button', { name: 'Save page' }).boundingBox())!
  const next = (await page.getByRole('button', { name: 'Next' }).boundingBox())!
  const row = (await page.locator('.ce-actions').boundingBox())!
  expect(save.y).toBeGreaterThan(next.y + next.height - 1)
  expect(save.width).toBeGreaterThan(row.width - 2)

  await page.getByTestId('ce-box-12').tap()
  await expect(page.getByLabel('English')).toBeVisible()
  const viewport = page.viewportSize()!
  for (const y of [0, 200, 100000]) {
    await page.evaluate((to) => window.scrollTo(0, to), y)
    const apply = (await page.getByRole('button', { name: 'Apply' }).boundingBox())!
    expect(apply.y).toBeGreaterThanOrEqual(0)
    expect(apply.y + apply.height).toBeLessThanOrEqual(viewport.height)
  }
  // At the bottom the pinned bar must not hide the last field.
  const swatch = (await page.getByRole('button', { name: '#b3261e' }).boundingBox())!
  const apply = (await page.getByRole('button', { name: 'Apply' }).boundingBox())!
  expect(swatch.y + swatch.height).toBeLessThanOrEqual(apply.y)
})

test('draw by touch drag and reach Delete box beside the fixed Apply bar', async ({ page }) => {
  const s = await mockEditor(page)
  await page.goto('/#/comic/7/edit?page=1')
  await expect(page.getByTestId('ce-box-12')).toBeVisible()
  await page.getByRole('button', { name: 'Draw' }).tap()
  const b = (await page.getByTestId('ce-stage').boundingBox())!
  const at = (px: number, py: number) => ({ x: b.x + (px / 800) * b.width, y: b.y + (py / 1200) * b.height })
  const cdp = await page.context().newCDPSession(page)
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [at(200, 800)] })
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [at(350, 850)] })
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [at(500, 900)] })
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
  await expect(page.getByLabel('English')).toBeVisible()
  expect(s.posts).toHaveLength(1)
  expect(s.posts[0].rev).toBe(1)
  expect(Math.abs(s.posts[0].region!.w - 300)).toBeLessThan(12)

  const del = page.getByRole('button', { name: 'Delete box' })
  const apply = page.getByRole('button', { name: 'Apply' })
  const viewport = page.viewportSize()!
  for (const y of [0, 100000]) {
    await page.evaluate((to) => window.scrollTo(0, to), y)
    const d = (await del.boundingBox())!
    const a = (await apply.boundingBox())!
    expect(d.y).toBeGreaterThanOrEqual(0)
    expect(d.y + d.height).toBeLessThanOrEqual(viewport.height)
    expect(d.x + d.width).toBeLessThanOrEqual(a.x + 1)
    expect(d.height).toBeGreaterThanOrEqual(44)
  }
  await del.tap()
  await expect(page.getByTestId('ce-box-32')).toHaveCount(0)
  expect(s.posts.map((p) => p.method)).toEqual(['POST', 'DELETE'])
  expect(s.posts[1].rev).toBe(2)
  await page.getByRole('button', { name: 'Undo' }).click()
  await expect(page.getByTestId('ce-box-32')).toBeVisible()
  await expectNoSidewaysScroll(page)
  expect(s.comic.unmocked).toEqual([])
})
