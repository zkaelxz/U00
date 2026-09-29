import { test, type Page } from '@playwright/test'

// Review screenshots for the Library parity batch (P06, L01, L04, L05, P03).
// Skipped unless LIB_SHOTS_DIR=<dir> is set. Stats, costs and series are
// mocked with a realistic state; nothing is written.

const DIR = process.env.LIB_SHOTS_DIR
test.skip(!DIR, 'set LIB_SHOTS_DIR to save screenshots')

const stats = {
  total_dramas: 5,
  by_status: { new: 1, transcribed: 2, translated: 2 },
  by_media_type: { audio_drama: 3, manhua: 1, novel: 1 },
  total_lines: 4210,
  translated_lines: 2875,
  usage: { input_tokens: 812000, output_tokens: 301000, cache_read_tokens: 243600, estimated_cost_usd: 3.47, call_count: 318 },
}

const costs = {
  items: [
    { id: 1, title_en: 'Grandmaster of Demonic Cultivation', title_zh: '魔道祖师', translation_engine: 'claude', input_tokens: 540000, output_tokens: 201000, cache_read_tokens: 162000, estimated_cost_usd: 3.47, call_count: 212 },
    { id: 2, title_en: "Heaven Official's Blessing", title_zh: '天官赐福', translation_engine: 'nllb', input_tokens: 272000, output_tokens: 100000, cache_read_tokens: 0, estimated_cost_usd: 0, call_count: 106 },
  ],
}

const ref = (id: number, title_en: string, title_zh: string, media_type: string, status: string) =>
  ({ id, title_en, title_zh, media_type, status, updated_at: '2026-09-29T12:00:00' })

const series = {
  items: [
    {
      id: 1, name: 'Mo Dao Zu Shi', character_count: 14, glossary_term_count: 37,
      dramas: [
        ref(1, 'Grandmaster of Demonic Cultivation', '魔道祖师', 'audio_drama', 'translated'),
        ref(4, 'MDZS (manhua)', '魔道祖师漫画', 'manhua', 'transcribed'),
        ref(5, 'MDZS (novel)', '魔道祖师小说', 'novel', 'new'),
      ],
    },
    { id: 2, name: 'Tian Guan Ci Fu', character_count: 3, glossary_term_count: 5, dramas: [ref(2, "Heaven Official's Blessing", '天官赐福', 'audio_drama', 'translated')] },
  ],
}

async function mock(page: Page) {
  await page.route('**/api/library/stats', (r) => r.fulfill({ json: stats }))
  await page.route('**/api/library/costs', (r) => r.fulfill({ json: costs }))
  await page.route('**/api/library/series', (r) => r.fulfill({ json: series }))
}

async function openTools(page: Page) {
  const tools = page.getByRole('region', { name: 'Library tools' })
  for (const title of ['Series', 'Cost by drama']) {
    const summary = tools.locator('summary', { hasText: title }).first()
    if (await summary.count()) await summary.click()
  }
}

for (const [name, viewport] of [['desktop', { width: 1280, height: 900 }], ['phone', { width: 390, height: 844 }]] as const) {
  test(`library parity screenshots (${name})`, async ({ page }) => {
    await page.setViewportSize(viewport)
    await mock(page)
    await page.goto('/')
    await page.getByRole('heading', { name: 'Library', exact: true }).waitFor()
    await page.waitForTimeout(300)
    await page.screenshot({ path: `${DIR}/library-top-${name}.png` })

    await openTools(page)
    const tools = page.getByRole('region', { name: 'Library tools' })
    await tools.scrollIntoViewIfNeeded()
    await tools.screenshot({ path: `${DIR}/library-tools-${name}.png` })

    await page.getByRole('button', { name: 'New drama' }).first().click()
    const dialog = page.getByRole('dialog')
    await dialog.waitFor()
    await dialog.screenshot({ path: `${DIR}/new-drama-${name}.png` })
  })
}
