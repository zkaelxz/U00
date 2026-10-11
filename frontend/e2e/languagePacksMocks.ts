import type { Page, Route } from '@playwright/test'

// A small stand-in for /api/language-packs so the section can be driven without
// writing to the shared seeded library.
const STYLES = {
  default: 'romanised',
  options: { romanised: 'Keep honorifics romanised (for example senpai, -san)', natural: 'Translate them into natural English' },
}

const packFor = (id: string, title: string, enabled: boolean, style: string | null) => ({
  id, version: 1, language: 'ja', title, description: 'Starter pack, review it. Draft entries.',
  entry_count: 2, styles: id === 'ja-address' ? STYLES : { default: '', options: {} }, enabled, style,
})

export const ENTRIES = [
  { source: '先輩', en: { romanised: 'senpai', natural: 'senior' }, category: 'honorific', note: '', context: 'junior to senior' },
  { source: 'さん', en: { romanised: '-san', natural: 'Mr. or Ms.' }, category: 'honorific', note: 'Attach to the name.', context: '' },
]

export interface PackMock { posts: unknown[]; terms: unknown[]; defaults: unknown[] }

export async function mockLanguagePacks(page: Page): Promise<PackMock> {
  const seen: PackMock = { posts: [], terms: [], defaults: [] }
  let on: Record<string, string | null> = {}
  const title = () => ({
    source_language: 'ja', uses_default: false,
    packs: [
      packFor('ja-address', 'Japanese honorifics and address terms', 'ja-address' in on, on['ja-address'] ?? 'romanised'),
      packFor('ja-common', 'Japanese kinship, roles and set phrases', 'ja-common' in on, null),
    ],
  })
  await page.route('**/api/language-packs/dramas/1', (route: Route) => {
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON() as { packs: Record<string, string | null> }
      seen.posts.push(body)
      on = body.packs
    }
    return route.fulfill({ json: title() })
  })
  await page.route('**/api/language-packs/packs/ja-address', (route) =>
    route.fulfill({ json: { ...packFor('ja-address', 'Japanese honorifics and address terms', false, null), entries: ENTRIES } }),
  )
  await page.route('**/api/language-packs/defaults/ja', (route) => {
    seen.defaults.push(route.request().postDataJSON())
    return route.fulfill({ json: { language: 'ja', packs: Object.keys(on) } })
  })
  await page.route('**/api/glossary/dramas/1/terms', (route) => {
    if (route.request().method() === 'POST') {
      seen.terms.push(route.request().postDataJSON())
      return route.fulfill({ json: { id: 99, ...route.request().postDataJSON(), enforce_exact: false, aliases: [], banned_translations: [] } })
    }
    return route.fulfill({ json: [] })
  })
  return seen
}

export async function inSeriesSeven(page: Page) {
  await page.route('**/api/library/dramas/1', async (route) => {
    const resp = await route.fetch()
    await route.fulfill({ response: resp, json: { ...(await resp.json()), series_id: 7 } })
  })
}
