import type { Page, Route } from '@playwright/test'

import { GET_FIXTURES, ME, mockAuth, USER } from './authMocks'
import { expect, test } from './fixtures'

// The top bar in words: which words show at which width, that it stays one line, and the Connected word.
// Docked at 1440 the room is generous; the narrow cases use the longest ordinary header (the PIN profile
// button, a 12-letter account name, one running job, three new alerts) because that is what the steps are sized for.

const json = (route: Route, body: unknown) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
const NOW = () => Math.floor(Date.now() / 1000)

async function dock(page: Page, mode: 'docked' | 'float' | 'minimized' = 'docked') {
  await page.addInitScript((m) => localStorage.setItem('baihe.navDock', m), mode)
}

async function quietHeader(page: Page) {
  await page.route((u) => u.pathname === '/api/jobs', (r) => json(r, { items: [] }))
  await page.route((u) => u.pathname === '/api/notifications', (r) => json(r, { items: [] }))
}

/** Signed in as a 12-letter name (or the one given), the PIN profile button on, one job running and three new alerts. */
async function longestHeader(page: Page, name = 'Wolfgangmaxi') {
  const running = { job_id: 'r1', status: 'running', description: 'Transcribe Episode 12', progress: null, message: '', error: null, gpu_touching: false, updated_at: NOW(), owned_by_me: true, started_at: NOW() - 60, finished_at: null }
  const s = await mockAuth(page, { ...ME.signedIn, user: { ...USER, display_name: name } })
  await page.route((u) => u.pathname === '/api/meta', (r) => json(r, { ...(GET_FIXTURES['/api/meta'] as object), pc_profile: { pin_set: true, guest: false } }))
  await page.route((u) => u.pathname === '/api/jobs', (r) => json(r, { items: [running] }))
  await page.route((u) => u.pathname === '/api/notifications', (r) => json(r, { items: [1, 2, 3].map((id) => ({ id, at: NOW() - id, kind: 'job_done', text: `Export finished ${id}` })) }))
  await page.addInitScript(() => sessionStorage.setItem('baihe.pcProfilePicked', '1'))
  return s
}

interface HeaderState {
  words: string[]
  rowHeight: number
  tallest: number
  centres: number[]
  clipped: string[]
  sideways: boolean
}

/** What the header's right end shows: the words of each visible control in order, its height, and whether anything is cut. */
async function headerState(page: Page): Promise<HeaderState> {
  return page.evaluate(() => {
    const header = document.querySelector('.app-header') as HTMLElement
    const end = header.querySelector('.header-end') as HTMLElement
    const controls = [...end.children].filter((c) => c.getClientRects().length > 0) as HTMLElement[]
    const words = controls.map((c) =>
      c.matches('fieldset') ? [...c.querySelectorAll('label')].map((l) => (l as HTMLElement).innerText.trim()).join(' | ') : c.innerText.replace(/\s+/g, ' ').trim(),
    )
    const rects = controls.map((c) => c.getBoundingClientRect())
    const clipped = [...end.querySelectorAll<HTMLElement>('*')]
      // Only boxes that can hide their overflow count: the count badge on an icon is meant to poke out of its button.
      .filter((el) => el.getClientRects().length > 0 && !el.closest('.visually-hidden') && getComputedStyle(el).overflowX !== 'visible' && el.scrollWidth > el.clientWidth + 1)
      .map((el) => el.className || el.tagName)
    return {
      words,
      rowHeight: header.getBoundingClientRect().height,
      tallest: Math.max(...rects.map((r) => r.height)),
      centres: rects.map((r) => Math.round(r.top + r.height / 2)),
      clipped,
      sideways: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    }
  })
}

// The header's own padding and rule around one row of controls: anything taller than this has wrapped.
const HEADER_FRAME = 22

const ALL_WORDS = ['Simple | Advanced', 'Search pages and titles · Ctrl+K', 'Report a problem', 'Jobs', 'Alerts', 'Shortcuts', 'Theme', '● Connected']

test('docked at 1440 the bar reads in words, in order, on one line with nothing cut', async ({ page }) => {
  await dock(page)
  await quietHeader(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/#/library')
  await expect(page.getByTestId('api-connected')).toBeVisible()
  const s = await headerState(page)
  expect(s.words).toEqual(ALL_WORDS)
  expect(s.clipped).toEqual([])
  expect(s.rowHeight - s.tallest).toBeLessThanOrEqual(HEADER_FRAME)
  expect(Math.max(...s.centres) - Math.min(...s.centres)).toBeLessThanOrEqual(2)
  expect(s.sideways).toBe(false)
  // Words, not icons: no control still wears its flag, bell or search glass.
  await expect(page.locator('.header-end .hb-icon:visible')).toHaveCount(0)
})

test('the longest header gives its words up in steps: Report a problem, then the search words, then the five', async ({ page }) => {
  const s = await longestHeader(page)
  await dock(page)
  const widths = [
    { width: 1680, words: ['Simple | Advanced', 'Search pages and titles · Ctrl+K', 'Report a problem', 'Jobs 1', 'Alerts 3', 'Shortcuts', 'Theme', '● Connected', 'Owner · Switch profile', 'Wolfgangmaxi ▾'] },
    { width: 1560, words: ['Simple | Advanced', 'Search pages and titles · Ctrl+K', '', 'Jobs 1', 'Alerts 3', 'Shortcuts', 'Theme', '● Connected', 'Owner · Switch profile', 'Wolfgangmaxi ▾'] },
    { width: 1450, words: ['Simple | Advanced', 'Ctrl+K', '', 'Jobs 1', 'Alerts 3', 'Shortcuts', 'Theme', '● Connected', 'Owner · Switch profile', 'Wolfgangmaxi ▾'] },
    { width: 1120, words: ['Simple | Advanced', 'Ctrl+K', '', '1', '3', '?', '', 'Owner · Switch profile', 'W'] },
    // The narrowest window with the docked menu open (it is 216px wide): icons, still one line.
    { width: 1100, words: ['Simple | Advanced', 'Ctrl+K', '', '1', '3', '?', '', 'Owner · Switch profile', 'W'] },
  ]
  for (const { width, words } of widths) {
    await page.setViewportSize({ width, height: 800 })
    // A fresh load each time: a second hash visit would add the Back button, which takes room of its own.
    await page.goto('about:blank')
    await page.goto('/#/library')
    // The counts arrive from their own reads; measure only once they are in.
    await expect(page.getByTestId('jobs-count')).toHaveText('1')
    await expect(page.getByTestId('notify-count')).toHaveText('3')
    // Connected is drawn only from the step where the five show words, so below it the row is as narrow as it always was.
    if (words.includes('● Connected')) await expect(page.getByTestId('api-connected')).toBeVisible()
    else await expect(page.getByTestId('api-connected')).toBeHidden()
    const state = await headerState(page)
    expect(state.words, `${width}px`).toEqual(words)
    expect(state.rowHeight - state.tallest, `${width}px is one line`).toBeLessThanOrEqual(HEADER_FRAME)
    expect(state.clipped, `${width}px`).toEqual([])
    expect(state.sideways, `${width}px`).toBe(false)
  }
  // At 1120 the five are icons, and each is still named.
  const header = page.locator('.app-header')
  await expect(header.getByRole('button', { name: 'Search pages and titles', exact: true })).toBeVisible()
  await expect(header.getByRole('button', { name: 'Report a problem', exact: true })).toBeVisible()
  await expect(header.getByRole('button', { name: 'Jobs (1 job running)' })).toBeVisible()
  await expect(header.getByRole('button', { name: 'Alerts (3 new)' })).toBeVisible()
  await expect(header.getByRole('button', { name: 'Keyboard shortcuts' })).toBeVisible()
  await expect(header.getByRole('button', { name: /^Theme: .*Change theme$/ })).toBeVisible()
  await expect(header.locator('summary[aria-label="Account: Wolfgangmaxi"]')).toBeVisible()
  expect(s.unmocked).toEqual([])
})

test('a pop-out window has the same words, sized by the room its header has', async ({ page }) => {
  await quietHeader(page)
  await page.addInitScript(() => { window.name = 'baihe-pop-1' })
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.goto('/#/library')
  await expect(page.getByTestId('api-connected')).toBeVisible()
  await expect(page.locator('.popout-menu')).toBeVisible()
  const s = await headerState(page)
  expect(s.words).toEqual(ALL_WORDS)
  expect(s.rowHeight - s.tallest).toBeLessThanOrEqual(HEADER_FRAME)
})

test('Connected: nothing before the first check answers, then Connected, Can\'t reach Baihe when a check fails, and Connected again', async ({ page }) => {
  await dock(page)
  let release!: () => void
  const held = new Promise<void>((resolve) => { release = resolve })
  let failing = false
  // Every /api/meta read waits, so whichever part of the page asks first (the status check is one of several) is held.
  await page.route((u) => u.pathname === '/api/meta', async (route) => {
    await held
    if (failing) return route.abort()
    return route.fallback()
  })
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/#/library')
  await expect(page.locator('.header-end')).toBeVisible()
  await expect(page.getByTestId('api-connected')).toHaveCount(0)
  await expect(page.getByTestId('api-status')).toHaveCount(0)

  release()
  await expect(page.getByTestId('api-connected')).toHaveText('● Connected')
  await expect(page.getByTestId('api-status')).toHaveCount(0)

  failing = true
  await page.evaluate(() => window.dispatchEvent(new Event('focus')))
  await expect(page.getByTestId('api-status')).toBeVisible()
  await expect(page.getByTestId('api-connected')).toHaveCount(0)

  failing = false
  await page.evaluate(() => window.dispatchEvent(new Event('focus')))
  await expect(page.getByTestId('api-connected')).toBeVisible()
  await expect(page.getByTestId('api-status')).toHaveCount(0)
})

test('Connected is quiet for a screen reader: the dot is hidden and it is not a live region', async ({ page }) => {
  await dock(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/#/library')
  const connected = page.getByTestId('api-connected')
  await expect(connected).toBeVisible()
  await expect(connected.locator('[aria-hidden="true"]')).toHaveText('●')
  await expect(connected.locator('xpath=ancestor-or-self::*[@role="status" or @aria-live]')).toHaveCount(0)
})

test('Alerts: the name with and without new rows, and the panel heading', async ({ page }) => {
  await dock(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.route((u) => u.pathname === '/api/jobs', (r) => json(r, { items: [] }))
  const items = [1, 2].map((id) => ({ id, at: NOW() - id, kind: 'job_done', text: `Export finished ${id}` }))
  await page.route((u) => u.pathname === '/api/notifications', (r) => json(r, { items }))
  await page.goto('/#/library')
  const bell = page.getByRole('button', { name: /^Alerts/ })
  await expect(bell).toHaveAccessibleName('Alerts (2 new)')
  await expect(bell).toContainText('Alerts')
  await expect(page.getByTestId('notify-count')).toHaveText('2')
  await bell.click()
  await expect(page.getByRole('region', { name: 'Recent alerts' })).toBeVisible()
  await expect(page.getByTestId('notify-count')).toHaveCount(0)
  await expect(bell).toHaveAccessibleName('Alerts')
  await expect(page.getByText('Recent notifications')).toHaveCount(0)
})

// Heights of the same header on the commit before the words, at the same widths (docked menu); with Back the base wrapped the row to a line of its own.
const NARROW = [
  { width: 640, header: 'plain' as const, back: false, tallest: 69 },
  { width: 900, header: 'longest' as const, back: false, tallest: 80 },
  { width: 700, header: 'plain' as const, back: true, tallest: 124 },
  { width: 800, header: 'longest' as const, back: true, tallest: 124 },
]

/** Pairs of visible header controls (or a control and the Back button) whose boxes cross. */
async function overlaps(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const header = document.querySelector('.app-header') as HTMLElement
    const boxes = [...header.querySelectorAll('.header-end > *, .app-back')]
      .filter((el) => el.getClientRects().length > 0)
      .map((el) => ({ name: (el as HTMLElement).innerText.replace(/\s+/g, ' ').trim().slice(0, 20) || el.className || el.tagName, r: el.getBoundingClientRect() }))
    const found: string[] = []
    for (let a = 0; a < boxes.length; a++) for (let b = a + 1; b < boxes.length; b++) {
      const x = boxes[a].r, y = boxes[b].r
      if (x.left < y.right - 1 && y.left < x.right - 1 && x.top < y.bottom - 1 && y.top < x.bottom - 1) found.push(`${boxes[a].name} / ${boxes[b].name}`)
    }
    return found
  })
}

for (const { width, header, back, tallest } of NARROW) {
  test(`at ${width}px${back ? ' with Back' : ''}, ${header} header: no taller than before the words, nothing overlaps or scrolls sideways, and nothing says Connected`, async ({ page }) => {
    await dock(page)
    if (header === 'longest') await longestHeader(page)
    else await quietHeader(page)
    await page.setViewportSize({ width, height: 800 })
    await page.goto('/#/library')
    await expect(page.locator('.header-end')).toBeVisible()
    // A second hash visit in the same document is what gives the header its Back button.
    if (back) {
      await page.evaluate(() => { location.hash = '#/jobs' })
      await expect(page.locator('.app-back')).toBeVisible()
    }
    // The element is there once the status check has answered; it must stay out of sight below the step where the five show words.
    await expect(page.getByTestId('api-connected')).toHaveCount(1)
    await expect(page.getByTestId('api-connected')).toBeHidden()
    const s = await headerState(page)
    expect(Math.round(s.rowHeight)).toBeLessThanOrEqual(tallest)
    expect(s.sideways).toBe(false)
    expect(await overlaps(page)).toEqual([])
  })
}

// 24 characters is the longest name the button shows whole; one more and it says Account.
const NAME_24 = 'Wolfgang Amadeus Mozarts'
for (const [name, shown] of [[NAME_24, NAME_24], [`${NAME_24}!`, 'Account']]) {
  test(`the account button for a ${name.length}-character name says ${shown === name ? 'the name' : 'Account'}, and the panel has the whole name`, async ({ page }) => {
    await dock(page)
    await page.setViewportSize({ width: 1680, height: 900 })
    await mockAuth(page, { ...ME.signedIn, user: { ...USER, display_name: name } })
    await quietHeader(page)
    await page.goto('/#/library')
    const account = page.locator(`summary[aria-label="Account: ${name}"]`)
    await expect(account).toBeVisible()
    await expect.poll(() => account.evaluate((el) => (el as HTMLElement).innerText.replace(/\s+/g, ' ').trim())).toBe(`${shown} ▾`)
    const s = await headerState(page)
    expect(s.clipped).toEqual([])
    expect(s.sideways).toBe(false)
    await account.click()
    await expect(page.getByTestId('user-name')).toHaveText(name)
  })
}

test('a 24-character account name asks for more room before the words show, and the bar stays one line', async ({ page }) => {
  const name = 'Wolfgang Amadeus Mozarts'
  await longestHeader(page, name)
  await dock(page)
  // The 12-letter header has Jobs, Alerts, Shortcuts and Theme in words from about 1390px; this one needs about 6rem (90px) more.
  const widths = [
    { width: 1440, five: false },
    { width: 1480, five: true },
    { width: 1750, five: true },
  ]
  for (const { width, five } of widths) {
    await page.setViewportSize({ width, height: 800 })
    await page.goto('about:blank')
    await page.goto('/#/library')
    await expect(page.getByTestId('jobs-count')).toHaveText('1')
    await expect(page.getByTestId('notify-count')).toHaveText('3')
    const s = await headerState(page)
    expect(s.words.includes(`${name} ▾`), `${width}px shows the name`).toBe(five)
    expect(s.rowHeight - s.tallest, `${width}px is one line`).toBeLessThanOrEqual(HEADER_FRAME)
    expect(s.clipped, `${width}px`).toEqual([])
    expect(s.sideways, `${width}px`).toBe(false)
  }
})

test('Can\'t reach Baihe is wider than Connected: just above each words step the badge stays whole and the bar one line', async ({ page }) => {
  await longestHeader(page)
  await dock(page)
  let failing = false
  // Registered after the header's own /api/meta mock, so it sees every read first.
  await page.route((u) => u.pathname === '/api/meta', (route) => (failing ? route.abort() : route.fallback()))
  await page.setViewportSize({ width: 1700, height: 800 })
  await page.goto('/#/library')
  await expect(page.getByTestId('api-connected')).toBeVisible()
  failing = true
  await page.evaluate(() => window.dispatchEvent(new Event('focus')))
  const badge = page.getByTestId('api-status')
  await expect(badge).toBeVisible()
  // 1376, 1526 and 1640 are just above where the five, the search words and Report a problem start when "Connected" shows (the badge is 54px
  // wider, so the steps wait for it); 1430, 1580 and 1685 are where they start while it shows.
  for (const width of [1376, 1526, 1640, 1430, 1580, 1685]) {
    await page.setViewportSize({ width, height: 800 })
    await expect.poll(async () => (await badge.boundingBox())?.height ?? 0, { message: `${width}px` }).toBeGreaterThan(0)
    const s = await headerState(page)
    const box = (await badge.boundingBox())!
    expect(box.height, `${width}px: the badge is one line`).toBeLessThan(34)
    expect(box.width, `${width}px: the badge keeps its width`).toBeGreaterThanOrEqual(135)
    expect(s.tallest, `${width}px: no control wrapped its words`).toBeLessThanOrEqual(44)
    expect(s.rowHeight - s.tallest, `${width}px is one line`).toBeLessThanOrEqual(HEADER_FRAME)
    expect(s.clipped, `${width}px`).toEqual([])
    expect(await overlaps(page), `${width}px`).toEqual([])
    // The controls stay inside their own row: a negative margin must not push the first one out over the page title.
    expect(await page.evaluate(() => {
      const end = document.querySelector('.header-end') as HTMLElement
      const first = [...end.children].find((c) => c.getClientRects().length > 0) as HTMLElement
      return Math.round(end.getBoundingClientRect().left - first.getBoundingClientRect().left)
    }), `${width}px`).toBeLessThanOrEqual(1)
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth), `${width}px sideways`).toBeLessThanOrEqual(0)
  }
})
