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

/** Signed in as a 12-letter name, the PIN profile button on, one job running and three new alerts. */
async function longestHeader(page: Page) {
  const running = { job_id: 'r1', status: 'running', description: 'Transcribe Episode 12', progress: null, message: '', error: null, gpu_touching: false, updated_at: NOW(), owned_by_me: true, started_at: NOW() - 60, finished_at: null }
  const s = await mockAuth(page, { ...ME.signedIn, user: { ...USER, display_name: 'Wolfgangmaxi' } })
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

// Heights of the same header on the commit before the words, at the same widths (docked menu, no Back button).
const NARROW = [
  { width: 640, header: 'plain' as const, tallest: 69 },
  { width: 900, header: 'longest' as const, tallest: 80 },
]
for (const { width, header, tallest } of NARROW) {
  test(`at ${width}px, ${header} header: no taller than before the words, and nothing says Connected`, async ({ page }) => {
    await dock(page)
    if (header === 'longest') await longestHeader(page)
    else await quietHeader(page)
    await page.setViewportSize({ width, height: 800 })
    await page.goto('/#/library')
    await expect(page.locator('.header-end')).toBeVisible()
    // The element is there once the status check has answered; it must stay out of sight below the step where the five show words.
    await expect(page.getByTestId('api-connected')).toHaveCount(1)
    await expect(page.getByTestId('api-connected')).toBeHidden()
    const s = await headerState(page)
    expect(Math.round(s.rowHeight)).toBeLessThanOrEqual(tallest)
  })
}
