import { expect, test } from './fixtures'
import type { Page } from '@playwright/test'
import { hitHeight, installHitArea } from './hitArea'
import { holdShelfOf, placeholderAndShelf } from './continueShelfMocks'
import { expectNoSidewaysScroll } from './mockHelpers'

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// Phone (390x844): the More filters fold and the Continue shelf fit the width
// and keep 44px touch targets.

async function heightOf(page: Page, selector: string) {
  return (await hitHeight(page.locator(selector).first()))
}

test('More filters and Continue fit a phone', async ({ page }) => {
  await page.route('**/api/library/continue', (r) =>
    r.fulfill({
      json: {
        items: [1, 2, 3].map((id) => ({
          drama_id: id, title_en: `A rather long title title number ${id}`, title_zh: null, percent_complete: 30 + id,
          last_page: id, last_accessed_at: `2026-09-29T12:0${id}:00`, has_cover_art: false,
        })),
      },
    }),
  )
  await page.route('**/api/library/recent', (r) => r.fulfill({ json: { items: [] } }))
  await page.goto('/')
  const shelf = page.getByRole('region', { name: 'Continue' })
  // At most two items, and no More.
  await expect(shelf.getByRole('listitem')).toHaveCount(2)
  await expect(shelf.getByRole('button', { name: /^More/ })).toHaveCount(0)
  await page.getByRole('button', { name: 'Filters' }).tap()
  await page.getByText('More filters', { exact: true }).click()
  await expect(page.getByRole('checkbox', { name: 'wuxia' })).toBeVisible()
  await expectNoSidewaysScroll(page)
  expect(await heightOf(page, '.more-filters > summary')).toBeGreaterThanOrEqual(44)
  expect(await heightOf(page, '.tag-filter .check')).toBeGreaterThanOrEqual(44)
  await page.getByRole('button', { name: 'Close' }).tap()
  expect(await heightOf(page, '.continue-item .btn')).toBeGreaterThanOrEqual(44)
})

test('at 360px the loading grid matches the loaded card height and a long title does not scroll the page', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 780 })
  let release!: () => void
  const gate = new Promise<void>((r) => { release = r })
  await page.route((u) => u.pathname === '/api/library/dramas', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    await gate
    const res = await route.fetch()
    const body = await res.json()
    const one = { ...body.items.find((d: { id: number }) => d.id === 1), title_en: 'Signal', custom_tags: [] }
    const long = { ...one, id: 2, title_en: 'x'.repeat(130), title_zh: '魔'.repeat(130) }
    await route.fulfill({ response: res, json: { ...body, items: [one, long], count: 2 } })
  })
  await page.goto('/#/library')
  const skeleton = (await page.locator('.drama-grid.skeleton .drama-card').first().boundingBox())!.height
  release()
  const card = page.locator('.drama-grid:not(.skeleton) .drama-card').first()
  await expect(card).toBeVisible()
  expect(Math.abs((await card.boundingBox())!.height - skeleton)).toBeLessThanOrEqual(4)
  await expectNoSidewaysScroll(page)
})

for (const width of [390, 320]) {
  test(`at ${width}px each Continue button takes its own full-width row, 44px tall, with nothing cut off`, async ({ page }) => {
    await page.setViewportSize({ width, height: 780 })
    const long = 'A rather long title that needs two lines on a phone'
    await page.route('**/api/library/continue', (r) => r.fulfill({ json: { items: [{
      drama_id: 2, title_en: long, title_zh: null, percent_complete: 40, last_page: 1234,
      last_accessed_at: '2026-11-10T12:00:09', has_cover_art: false,
    }] } }))
    await page.route('**/api/library/recent', (r) => r.fulfill({ json: { items: [
      { id: 1, title_en: 'Grandmaster of Demonic Cultivation', title_zh: null, status: 'aligned', media_type: 'audio_drama', updated_at: '2026-11-10T12:00:08' },
      { id: 3, title_en: 'Signal', title_zh: null, status: 'translated', media_type: 'video_drama', updated_at: '2026-11-10T12:00:07' },
    ] } }))
    await page.route((u) => u.pathname === '/api/library/dramas', async (route) => {
      if (route.request().method() !== 'GET') return route.fallback()
      const res = await route.fetch()
      const body = await res.json()
      const counts: Record<number, object> = {
        1: { line_count: 20000, translated_count: 8766, status: 'aligned' },
        3: { line_count: 40, translated_count: 40, status: 'translated' },
      }
      await route.fulfill({ response: res, json: { ...body, items: body.items.map((d: { id: number }) => ({ ...d, ...counts[d.id] })) } })
    })
    await page.route('**/api/review/dramas/*/lines?*', (r) => r.fulfill({ status: 500, json: { error: { code: 'internal', message: 'No.' } } }))
    await page.goto('/')
    const shelf = page.getByRole('region', { name: 'Continue' })
    await expect(shelf.getByRole('link', { name: `Read from page 1234: ${long}`, exact: true })).toBeVisible()
    await expect(shelf.getByRole('link', { name: 'Translate 11,234 lines: Grandmaster of Demonic Cultivation', exact: true })).toBeVisible()
    const boxes = await shelf.locator('.continue-item').evaluateAll((items) => items.map((li) => {
      const btn = li.querySelector('.continue-next') as HTMLElement
      const text = li.querySelector('.continue-text') as HTMLElement
      const title = li.querySelector('.continue-title') as HTMLElement
      const inner = li.getBoundingClientRect()
      const b = btn.getBoundingClientRect()
      const t = text.getBoundingClientRect()
      const pad = parseFloat(getComputedStyle(li).paddingLeft) + parseFloat(getComputedStyle(li).paddingRight)
      // The label's own box: the button's scrollWidth also counts the ::after that widens its tap area.
      const range = document.createRange()
      range.selectNodeContents(btn)
      const label = range.getBoundingClientRect()
      return {
        below: b.top >= t.bottom, height: b.height, fullWidth: Math.abs(b.width - (inner.width - pad - 2)) <= 1,
        insideItem: b.left >= inner.left && b.right <= inner.right,
        buttonCut: label.left < b.left || label.right > b.right || label.bottom > b.bottom, titleCut: title.scrollWidth > title.clientWidth,
      }
    }))
    expect(boxes).toHaveLength(2)
    for (const box of boxes) {
      expect(box).toMatchObject({ below: true, fullWidth: true, insideItem: true, buttonCut: false, titleCut: false })
      expect(box.height).toBeGreaterThanOrEqual(44)
    }
    await expectNoSidewaysScroll(page)
    // The cards' quiet buttons (Listen, Watch, Details, Translate, Review) keep each word whole on one line.
    const broken = await page.locator('.drama-grid:not(.skeleton) .drama-card-foot .btn').evaluateAll((btns) => btns
      .filter((b) => {
        const range = document.createRange()
        range.selectNodeContents(b)
        return range.getClientRects().length > 1
      })
      .map((b) => b.textContent))
    expect(broken).toEqual([])
  })
}

test('at 390px the placeholder for three (one tile and More) has the loaded shelf\'s height', async ({ page }) => {
  const release = await holdShelfOf(page, 3)
  await page.goto('/')
  const h = await placeholderAndShelf(page, release)
  expect(Math.abs(h.shelf - h.placeholder), JSON.stringify(h)).toBeLessThanOrEqual(2)
})

test('the Running line fits a phone and is a 44px target', async ({ page }) => {
  await page.route('**/api/library/home', (r) => r.fulfill({ json: {
    running: [{ drama_id: 16, title: 'Chime: zatsudan', media_type: 'streamer_vod', content_mode: null, has_cover_art: false, updated_at: null,
      step: 'transcribe', status: 'running', job_id: null, started_at: null }],
    running_count: 2,
    can_control_queue: true,
  } }))
  await page.goto('/')
  await expect(page.getByTestId('home-running')).toContainText('Transcribing Chime: zatsudan')
  await expect(page.getByTestId('home-running')).toContainText('1 more is waiting its turn')

  await expectNoSidewaysScroll(page)
  const heights = await page.locator('.home-running').evaluateAll((els) => els.map((e) => Math.round(window.hitHeight(e))))
  expect(heights.length).toBeGreaterThan(0)
  for (const h of heights) expect(h).toBeGreaterThanOrEqual(44)
})

test('with nothing running, no shelf block is drawn above the list', async ({ page }) => {
  await page.route('**/api/library/home', (r) => r.fulfill({ json: {
    running: [], running_count: 0, can_control_queue: true,
  } }))
  await page.goto('/')
  await expect(page.locator('.drama-card').first()).toBeVisible()
  await expect(page.getByTestId('home-shelves')).toBeHidden()
})
