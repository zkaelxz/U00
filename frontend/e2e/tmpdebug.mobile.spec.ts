import { expect, test } from '@playwright/test'

import { mockEditor } from './comicEditorMocks'
import { expectNoSidewaysScroll } from './mockHelpers'

// Phone project: the comic editor stacks the page above the side panel.

test('draw by touch drag and reach Delete box beside the fixed Apply bar', async ({ page }) => {
  const s = await mockEditor(page)
  await page.goto('/#/comic/7/edit?page=1')
  await expect(page.getByTestId('ce-box-12')).toBeVisible()
  await page.evaluate(() => { (window as any).__d = []; for (const t of ['pointerdown','pointermove','pointerup','pointercancel','touchstart','touchmove','touchend','touchcancel','click','scroll','lostpointercapture']) window.addEventListener(t, (e) => (window as any).__d.push(Math.round(performance.now()) + ' y' + Math.round(window.scrollY) + ' ' + t + (e.cancelable === false ? '(nc)' : '')), true) })
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
  await page.evaluate(() => { (window as any).__ev = []; for (const t of ['pointerdown','pointerup','pointercancel','touchstart','touchend','touchcancel','click','lostpointercapture','gotpointercapture','scroll','resize']) window.addEventListener(t, (e) => (window as any).__ev.push(Math.round(performance.now()) + ' y' + Math.round(window.scrollY) + ' ' + t + ':' + ((e.target as HTMLElement).className || (e.target as HTMLElement).tagName) + ':' + ((e.target as HTMLElement).textContent||'').slice(0,12)), true) })
  await del.tap()
  await page.waitForTimeout(700)
  console.log('DBG', JSON.stringify(s.posts.map((p) => p.method)), JSON.stringify(await page.evaluate(() => (window as any).__d)))
  await expect(page.getByTestId('ce-box-32')).toHaveCount(0)
  expect(s.posts.map((p) => p.method)).toEqual(['POST', 'DELETE'])
  expect(s.posts[1].rev).toBe(2)
  await page.getByRole('button', { name: 'Undo' }).click()
  await expect(page.getByTestId('ce-box-32')).toBeVisible()
  await expectNoSidewaysScroll(page)
  expect(s.comic.unmocked).toEqual([])
})
