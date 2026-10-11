import type { Page } from '@playwright/test'

// Developer Mode rows the seeded API does not list while its own Developer Mode is off.
const DEV_ROWS = [
  { key: 'qwen_vad_threshold', type: 'float', default: 0.5, label: 'Qwen3 detection: confidence', help: 'How sure the detector must be that a sound is speech, for the Qwen3 speech-detection engine.', unit: '', section: 'Voice detection', min: 0.2, max: 0.8 },
  { key: 'qwen_vad_pad_ms', type: 'int', default: 100, label: 'Qwen3 detection: padding', help: 'Audio kept on each side of detected speech.', unit: 'ms', section: 'Voice detection', min: 0, max: 500 },
  { key: 'split_min_piece_words', type: 'int', default: 2, label: 'Shortest piece (words)', help: 'Pieces with fewer words than this are joined to a neighbour.', unit: 'words', section: 'Line splitting', min: 1, max: 4 },
]

// The settings overview and schema, with only what the Timing and splitting card reads; the
// seeded API answers everything else. Writes are kept in `posts` and applied to the overview, and any
// other write is aborted and recorded in `unmocked`.
export async function mockTimingSettings(page: Page, opts: { developerMode?: boolean } = {}) {
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
      const prefs = overview.preferences as Record<string, unknown>
      Object.assign(prefs, {
        split_max_seconds: 8, split_max_cjk_chars: 40, quick_split_seconds: 12, gap_fill_min_gap_seconds: 2,
        scene_break_gap_seconds: 3, sentence_group_gap_seconds: 1,
        qwen_vad_threshold: 0.5, qwen_vad_pad_ms: 100, split_min_piece_words: 2,
      })
    }
    if (r.method() === 'GET') return route.fulfill({ json: overview })
    const body = r.postDataJSON() as Record<string, unknown>
    posts.push(body)
    Object.assign(overview.preferences as object, body)
    return route.fulfill({ json: overview })
  })
  await page.route('**/api/settings/schema', async (route) => {
    const real = await route.fetch()
    const json = (await real.json()) as { settings: Record<string, unknown>[] }
    const rows = json.settings.filter((row) => row.section !== 'Voice detection')
    if (opts.developerMode) {
      for (const dev of DEV_ROWS) {
        rows.push({ scope: 'developer', placeholder: '', tab: 'system', custom: false, dev_only: true, choices: null, max_len: null, multiline: false, ...dev })
      }
    }
    return route.fulfill({ json: { settings: rows } })
  })
  if (opts.developerMode) {
    await page.route('**/api/assistant/settings', (route) =>
      route.request().method() === 'GET' ? route.fulfill({ json: { developer_mode: true } }) : route.abort())
  }
  return { posts, unmocked }
}
