import type { Page } from '@playwright/test'

import { mockAuth, type AuthMockState, type MeBody } from './authMocks'

// Library sharing control: /api/auth/me, the Library lists and /api/sharing/*
// flips are mocked (everything else is authMocks' fixtures or aborted).

const base = {
  title_zh: null, author: null, studio: null, director: null, voice_actors: null, status: 'translated',
  source_language: 'zh', media_type: 'audio_drama', content_mode: 'audio_drama', translation_engine: 'claude',
  custom_tags: [], created_at: '2026-09-29T12:00:00', updated_at: '2026-09-29T12:00:00',
}

export const DRAMAS = [
  { ...base, id: 1, title_en: 'Hidden Letters', series_id: null, is_private: true, owned_by_me: true },
  { ...base, id: 2, title_en: 'Neighbour Tale', series_id: null, is_private: false, owned_by_me: false },
  { ...base, id: 3, title_en: 'Saga Ep 1', series_id: 9, is_private: false, owned_by_me: true },
  { ...base, id: 4, title_en: 'Saga Ep 2', series_id: 9, is_private: false, owned_by_me: true },
]

export const SERIES = [
  {
    id: 9, name: 'Saga', is_private: false, owned_by_me: true, character_count: 2, glossary_term_count: 1,
    dramas: [3, 4].map((id) => ({ id, title_en: `Saga Ep ${id - 2}`, title_zh: null, status: 'translated', media_type: 'audio_drama' })),
  },
]

export const CONFLICT = "Move other people's dramas out of this series first."

export interface SharingMock extends AuthMockState {
  posts: { path: string; body: unknown }[]
}

export async function mockLibrarySharing(page: Page, me: MeBody): Promise<SharingMock> {
  const s = (await mockAuth(page, me)) as SharingMock
  s.posts = []
  await page.route('**/api/library/dramas', (route) => route.fulfill({ json: { items: DRAMAS, count: DRAMAS.length } }))
  await page.route('**/api/library/series', (route) => route.fulfill({ json: { items: SERIES } }))
  await page.route('**/api/sharing/**', (route) => {
    const r = route.request()
    const m = new URL(r.url()).pathname.match(/^\/api\/sharing\/(dramas|series)\/(\d+)\/private$/)
    if (!m || r.method() !== 'POST') {
      s.unmocked.push(`${r.method()} ${r.url()}`)
      return route.abort()
    }
    const body = r.postDataJSON() as { private: boolean }
    s.posts.push({ path: new URL(r.url()).pathname, body })
    if (m[1] === 'series') {
      return route.fulfill({ status: 409, json: { error: { code: 'conflict', message: CONFLICT } } })
    }
    return route.fulfill({ json: { kind: 'drama', id: Number(m[2]), is_private: body.private } })
  })
  return s
}
