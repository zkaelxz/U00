import { execFileSync } from 'node:child_process'
import path from 'node:path'

import type { Page, Route } from '@playwright/test'

// Shared by review-resegment-llm(.mobile).spec.ts (parity R47): three real
// lines for drama 3 in the throwaway library, and mocks for the AI preview
// job, its GET, the apply (use_preview) and the job records. The translate
// config is the real one with the engine list and spend pinned.

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')

export function python(code: string) {
  execFileSync(process.env.PYTHON ?? 'python', ['-c', `import db\ndb.configure_library_dir(${JSON.stringify(libraryDir)})\n${code}`], { cwd: repoRoot })
}

export function seedLines() {
  python(`
from core import Line
db.update_drama(3, audio_filename=None, source_video_filename=None)
db.save_lines(3, [
    Line(idx=0, start=0.0, end=3.0, zh='你好我的朋友今天天气真的很好', en='Hello my friend, lovely day'),
    Line(idx=1, start=3.0, end=6.0, zh='我们一起去公园散步然后吃晚饭吧', en='', flag='uncertain', flag_note='check'),
    Line(idx=2, start=6.0, end=7.0, zh='谢谢', en='Thanks'),
])
`)
}

export const clearLines = () => python('db.save_lines(3, [])')

export const job = (id: string, status: string) => ({
  job_id: id, status, progress: status === 'running' ? 0.4 : null, message: '', error: null, description: null,
  gpu_touching: false, started_at: 1, finished_at: status === 'running' ? null : 2, updated_at: 1,
})

export const PREVIEW = {
  drama_id: 3,
  source_line_ids: [101, 102, 103],
  line_count_before: 3,
  line_count_after: 5,
  changed: [
    { line_id: 101, idx: 0, zh: '你好我的朋友今天天气真的很好', pieces: ['你好我的朋友', '今天天气真的很好'] },
    { line_id: 102, idx: 1, zh: '我们一起去公园散步然后吃晚饭吧', pieces: ['我们一起去公园散步', '然后吃晚饭吧'] },
  ],
  translated: 1,
  flagged: 1,
  notes: 0,
  needs_confirm: true,
  engine: 'gemini',
}

export interface Calls {
  previewStarts: Record<string, unknown>[]
  applies: Record<string, unknown>[]
}

/**
 * Mocks the AI preview flow. The GET is a 404 until a preview job was
 * started; `apply` answers POST .../resegment (default: starts resegment_3).
 */
export async function mockAiResegment(
  page: Page,
  opts: { preview?: typeof PREVIEW; apply?: (route: Route, body: Record<string, unknown>) => Promise<void> } = {},
): Promise<Calls> {
  const calls: Calls = { previewStarts: [], applies: [] }
  const preview = opts.preview ?? PREVIEW
  // The real config is read once and reused: re-fetching it in every
  // handler call fails with "Response has been disposed" when reads overlap.
  let realConfig: Promise<Record<string, unknown>> | null = null
  await page.route('**/api/translate-run/dramas/3/config', async (route) => {
    realConfig ??= route.fetch().then((r) => r.json())
    const real = await realConfig
    await route.fulfill({
      json: {
        ...real,
        translation_engine: 'claude',
        engines: [
          { name: 'claude', label: 'Claude', free: false, models: null, key_configured: true },
          { name: 'gemini', label: 'Gemini', free: false, models: ['flash', 'pro'], key_configured: true },
          { name: 'deepl', label: 'DeepL', free: false, models: null, key_configured: true },
        ],
        month_spend: 1.25,
        monthly_cap_usd: 20,
        cap_applies_by_engine: { claude: true, gemini: true, deepl: true },
      },
    })
  })
  await page.route('**/api/restructure/dramas/3/resegment/preview-llm', (route) => {
    if (route.request().method() === 'POST') {
      calls.previewStarts.push(route.request().postDataJSON())
      return route.fulfill({ json: { job_id: 'resegpreview_3', drama_id: 3 } })
    }
    return calls.previewStarts.length
      ? route.fulfill({ json: preview })
      : route.fulfill({ status: 404, json: { error: { code: 'not_found', message: 'No LLM re-segmentation preview is ready for this drama.' } } })
  })
  await page.route('**/api/jobs/resegpreview_3', (route) => route.fulfill({ json: job('resegpreview_3', 'done') }))
  await page.route('**/api/jobs/resegment_3', (route) => route.fulfill({ json: job('resegment_3', 'done') }))
  await page.route('**/api/restructure/dramas/3/resegment', async (route) => {
    const body = route.request().postDataJSON()
    calls.applies.push(body)
    if (opts.apply) return opts.apply(route, body)
    return route.fulfill({ json: { job_id: 'resegment_3', drama_id: 3 } })
  })
  return calls
}

/** Opens drama 3's Review stage with the Structure section and Use AI on. */
export async function openAiStructure(page: Page) {
  await page.goto('/#/drama/3/review')
  await page.locator('.review-line:not(.review-skeleton)').first().waitFor()
  const group = page.getByRole('group', { name: 'Structure' })
  await group.locator('summary', { hasText: 'Structure' }).click()
  await group.getByRole('switch', { name: 'Use AI' }).click()
  return group
}
