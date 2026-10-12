import { expect, test } from '@playwright/test'

// People and access on a phone (390x844, touch): the three cards fit without
// sideways scroll in light and dark, and their summaries are 44px targets.

const ME = {
  auth_enabled: true, signed_in: true, sign_in_configured: true, zone: 'internet',
  permissions: ['library.read', 'admin.diagnostics', 'admin.users.read', 'admin.users'],
  user: { id: 1, email: 'owner@example.com', display_name: 'Owner', is_admin: true, is_local_owner: false },
}
const USER = (id: number, email: string, extra: Record<string, unknown> = {}) => ({
  id, email, display_name: '', is_admin: false, is_active: true, has_google_binding: true,
  created_at: '2026-09-29T10:00:00Z', permissions: ['library.read'], active_sessions: 1, is_self: false, ...extra,
})

for (const scheme of ['light', 'dark'] as const) {
  test(`phone ${scheme}: People and access fits and its cards are tappable`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: scheme })
    await page.route('**/api/meta', (r) => r.fulfill({ json: { app: 'Baihe Studio', api_version: '0.1', environment: 'production', local: true } }))
    await page.route('**/api/auth/me', (r) => r.fulfill({ json: ME }))
    await page.route('**/api/jobs', (r) => r.fulfill({ json: { items: [], count: 0 } }))
    await page.route('**/api/admin/users', (r) => r.fulfill({ json: { users: [
      USER(1, 'owner@example.com', { is_admin: true, is_self: true }),
      USER(2, `a-very-long-address-${'x'.repeat(80)}@example.com`),
    ] } }))
    await page.route('**/api/admin/audit**', (r) => r.fulfill({ json: { events: [
      { id: 1, ts: '2026-09-30T12:01:00Z', user_id: 2, action: 'login.success', detail: `user 2 ip 203.0.113 ${'y'.repeat(120)}` },
    ], next_before_id: null, actions: ['login.success'] } }))
    await page.route('**/api/diagnostics/remote-health/ip-check', (r) => r.fulfill({ json: { configured: true } }))
    await page.goto('/#/admin')
    await expect(page.getByRole('heading', { name: 'People and access', level: 2 })).toBeVisible()
    const cards = page.locator('details.section')
    await expect(cards).toHaveCount(3)
    for (let n = 1; n < 3; n++) await cards.nth(n).locator('summary').tap()
    await expect(page.getByRole('table', { name: 'Activity log' })).toBeVisible()
    for (let n = 0; n < 3; n++) {
      const box = await cards.nth(n).locator('summary').boundingBox()
      expect(box!.height).toBeGreaterThanOrEqual(44)
    }
    const { scroll, client } = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }))
    expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
    if (process.env.JOBS_SCREENS_DIR) await page.screenshot({ path: `${process.env.JOBS_SCREENS_DIR}/admin-phone-${scheme}.png`, fullPage: true })
  })
}
