import { type Page, type Route } from '@playwright/test'

import { ME } from './authMocks'

export const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, json: body })

// The base every mocked spec starts from: GETs reach the seeded API, any write nothing else handles is
// aborted and recorded (a spec asserts the list stays empty so the shared library is never written), and
// the session is auth-off. Register it first: Playwright tries the most recent route first, so this one
// only catches what the spec's own routes leave alone.
export async function mockAppShell(page: Page): Promise<string[]> {
  const unmocked: string[] = []
  await page.route('**/api/**', (route) => {
    const r = route.request()
    if (r.method() === 'GET') return route.continue()
    unmocked.push(`${r.method()} ${r.url()}`)
    return route.abort()
  })
  await page.route(/\/api\/auth\/me$/, (route) => json(route, ME.authOff))
  return unmocked
}
