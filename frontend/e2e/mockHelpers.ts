import { expect, type Page, type Route } from '@playwright/test'

import { ME } from './authMocks'

// Resolves once no finite CSS transition or animation is still running (an endless spinner or a scroll-driven effect never ends), so a
// layout read after a hover or focus sees the end state rather than a frame in between.
export const animationsSettled = (page: Page) =>
  expect
    .poll(() =>
      page.evaluate(() =>
        document.getAnimations().filter((a) => a.timeline instanceof DocumentTimeline && a.playState === 'running' && a.effect?.getComputedTiming().endTime !== Infinity).length))
    .toBe(0)

// Runs the page's fake clock forward (page.clock.install() first, before goto) so a timer that should NOT
// have fired is proven absent without sitting out its interval. The evaluate round trip lets requests the
// timers started reach the route handlers before the caller counts them.
export async function runClock(page: Page, ms: number) {
  await page.clock.runFor(ms)
  await page.evaluate(() => undefined)
}

export const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, json: body })

// Catch-all for writes: GETs reach the seeded API, any other /api call nothing else handles is aborted and
// recorded (a spec asserts the list stays empty so the shared library is never written). Register it first:
// Playwright tries the most recent route first, so this one only catches what the spec's own routes leave alone.
export async function guardWrites(page: Page): Promise<string[]> {
  const unmocked: string[] = []
  await page.route('**/api/**', (route) => {
    const r = route.request()
    if (r.method() === 'GET') return route.fallback()
    unmocked.push(`${r.method()} ${r.url()}`)
    return route.abort()
  })
  return unmocked
}

// The base every mocked spec starts from: the write guard, and the session is auth-off.
export async function mockAppShell(page: Page): Promise<string[]> {
  const unmocked = await guardWrites(page)
  await page.route(/\/api\/auth\/me$/, (route) => json(route, ME.authOff))
  return unmocked
}

export async function expectNoSidewaysScroll(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}
