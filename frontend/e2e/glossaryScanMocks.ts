import type { Page, Route } from '@playwright/test'
import { ME } from './authMocks'
import { withExportLines } from './stageLineMocks'

// Glossary tabs and scan-resume specs: drama 1 reported as in series 7, terms, novel status and
// both extraction runs mocked, so no engine is ever called.
export const TERM = { id: 11, term_original: 'ヤロ', term_translation: 'Yalo', notes: '', category: null, policy: null, enforce_exact: false, aliases: [], banned_translations: [] }

export const prop = (term: string, en: string) => ({
  term, suggested_translation: en, category: 'person_name', policy: 'keep_pinyin', reason: 'Recurring name',
  already_in_glossary: false, occurrences: 3, alternatives: [], confidence: 'high',
})

export const IDLE = { job_id: '', status: 'idle', progress: 0, message: '', proposals: null, run_id: null }

export interface Held {
  status: string
  proposals?: object[] | null
  progress?: number
  message?: string
}

// What the server answers for one source; mutate `.held` to move a scan along while the page is open.
export function heldScan(first: Held | null = null) {
  const s = { held: first, polls: 0, posts: 0 }
  const handler = (route: Route) => {
    if (route.request().method() === 'POST') {
      s.posts += 1
      return route.fulfill({ json: { job_id: 'scan_1', engine: 'claude', line_count: 3 } })
    }
    s.polls += 1
    return route.fulfill({
      json: s.held ? { job_id: 'scan_1', run_id: 'run-1', progress: 0, message: '', proposals: null, ...s.held } : IDLE,
    })
  }
  return { state: s, handler }
}

export async function glossarySetup(page: Page, opts: { terms?: object[]; novel?: boolean; lines?: boolean; series?: boolean } = {}) {
  const { terms = [TERM], novel = false, lines = true, series = true } = opts
  // The Glossary section starts folded the first time; these specs need its body.
  await page.addInitScript(() => localStorage.setItem('baihe.section.translate.glossary', '1'))
  await page.route(/\/api\/auth\/me$/, (route) => route.fulfill({ json: ME.authOff }))
  if (series) {
    await page.route('**/api/library/dramas/1', async (route) => {
      const resp = await route.fetch()
      await route.fulfill({ response: resp, json: { ...(await resp.json()), series_id: 7 } })
    })
  }
  await page.route('**/api/characters/series/7/characters', (route) => route.fulfill({ json: [] }))
  await page.route('**/api/glossary/dramas/1/terms', (route) => route.fulfill({ json: terms }))
  await page.route('**/api/novel/dramas/1/status', (route) =>
    route.fulfill({ json: { drama_id: 1, has_novel_text: novel, char_count: novel ? 900 : 0, chapters: novel ? 3 : 0, ocr_running: false } }),
  )
  await withExportLines(page, 1, lines ? 3 : 0)
  await page.route('**/api/glossary/dramas/1/instructions', (route) =>
    route.fulfill({ json: { project_instructions: '', series_instructions: '' } }),
  )
}
