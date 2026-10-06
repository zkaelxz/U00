import type { Page } from '@playwright/test'

// Settings overview with only what the Uploads block reads; other reads go to the seeded API.
export async function mockUploadSettings(page: Page, opts: { fromEnv?: boolean } = {}) {
  const posts: Record<string, unknown>[] = []
  const unmocked: string[] = []
  let overview: Record<string, unknown> | null = null
  await page.route('**/api/**', (route) => {
    const r = route.request()
    if (r.method() === 'GET') return route.fallback()
    unmocked.push(`${r.method()} ${r.url()}`)
    return route.abort()
  })
  await page.route('**/api/settings', async (route) => {
    const r = route.request()
    if (!overview) {
      const real = await route.fetch()
      overview = (await real.json()) as Record<string, unknown>
      Object.assign(overview, {
        upload_max_mb_from_env: !!opts.fromEnv,
        effective_upload_max_mb: opts.fromEnv ? 500 : 20480,
      })
      ;(overview.preferences as Record<string, unknown>).max_upload_mb = 20480
    }
    if (r.method() === 'GET') return route.fulfill({ json: overview })
    const body = r.postDataJSON() as Record<string, unknown>
    posts.push(body)
    Object.assign(overview.preferences as object, body)
    if (!opts.fromEnv && 'max_upload_mb' in body) overview.effective_upload_max_mb = body.max_upload_mb
    return route.fulfill({ json: overview })
  })
  return { posts, unmocked }
}
