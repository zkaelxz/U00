import { expect, test } from '@playwright/test'

import { SID, cue, mockLive, openLive } from './liveMocks'

// Live > "Use glossary and packs from": a series or a title rides along with Start, is
// remembered per browser (live.options), and a remembered one that is gone is not sent.
// liveMocks.ts answers the library with the series "Moonlit" (id 3), its title "Chapter Two" (id 8)
// and the title "Moonlit Letters" (id 7, no series).

const URL_FIELD = 'https://www.youtube.com/watch?v=abc'
const OPTIONS_KEY = 'baihe.pref.live.options'

async function start(live: Awaited<ReturnType<typeof openLive>>) {
  await live.getByLabel('Stream link', { exact: true }).fill(URL_FIELD)
  await live.getByRole('button', { name: 'Start', exact: true }).click()
}

test('the picker lists series first, then titles under their series', async ({ page }) => {
  await mockLive(page)
  const live = await openLive(page)
  const picker = live.getByLabel('Use glossary and packs from', { exact: true })
  await expect(picker.locator('option')).toHaveText([
    'None', 'Series: Moonlit', 'Chapter Two (in Moonlit)', "Moonlit Letters (No series: only this title's packs)",
  ])
  await expect(picker).toHaveValue('')
})

test('None starts with the old request, without glossary_drama_id', async ({ page }) => {
  const m = await mockLive(page)
  const live = await openLive(page)
  const picker = live.getByLabel('Use glossary and packs from', { exact: true })
  await picker.selectOption('t:7')
  await picker.selectOption('')
  await start(live)
  await expect.poll(() => m.posts.length).toBe(1)
  expect(m.posts[0].body).not.toHaveProperty('glossary_drama_id')
  expect(m.unmocked).toEqual([])
})

test('the chosen title is remembered across a reload', async ({ page }) => {
  const m = await mockLive(page)
  const live = await openLive(page)
  await live.getByLabel('Use glossary and packs from', { exact: true }).selectOption('t:7')
  await expect.poll(() => page.evaluate((k) => localStorage.getItem(k), OPTIONS_KEY)).toContain('"glossary_drama_id":7')

  await page.reload()
  const again = page.getByRole('region', { name: 'Live' })
  await expect(again.getByLabel('Use glossary and packs from', { exact: true })).toHaveValue('t:7')
  await start(again)
  await expect.poll(() => m.posts.length).toBe(1)
  expect(m.posts[0].body).toMatchObject({ glossary_drama_id: 7 })
})

test('a remembered title that is no longer in the list is shown as None and not sent', async ({ page }) => {
  await page.addInitScript(([k]) => localStorage.setItem(k, JSON.stringify({ glossary_drama_id: 99 })), [OPTIONS_KEY])
  const m = await mockLive(page)
  const live = await openLive(page)
  const picker = live.getByLabel('Use glossary and packs from', { exact: true })
  await expect(picker.locator('option')).toHaveCount(4)
  await expect(picker).toHaveValue('')
  await start(live)
  await expect.poll(() => m.posts.length).toBe(1)
  expect(m.posts[0].body).not.toHaveProperty('glossary_drama_id')
})

test('a series pick sends series_id and no title, and is remembered', async ({ page }) => {
  const m = await mockLive(page)
  const live = await openLive(page)
  await live.getByLabel('Use glossary and packs from', { exact: true }).selectOption({ label: 'Series: Moonlit' })
  await expect.poll(() => page.evaluate((k) => localStorage.getItem(k), OPTIONS_KEY)).toContain('"series_id":3')
  await page.reload()
  const again = page.getByRole('region', { name: 'Live' })
  await expect(again.getByLabel('Use glossary and packs from', { exact: true })).toHaveValue('s:3')
  await start(again)
  await expect.poll(() => m.posts.length).toBe(1)
  expect(m.posts[0].body).toMatchObject({ series_id: 3 })
  expect(m.posts[0].body).not.toHaveProperty('glossary_drama_id')
})

test('a remembered series that is no longer in the list is not sent', async ({ page }) => {
  await page.addInitScript(([k]) => localStorage.setItem(k, JSON.stringify({ series_id: 99 })), [OPTIONS_KEY])
  const m = await mockLive(page)
  const live = await openLive(page)
  await expect(live.getByLabel('Use glossary and packs from', { exact: true })).toHaveValue('')
  await start(live)
  await expect.poll(() => m.posts.length).toBe(1)
  expect(m.posts[0].body).not.toHaveProperty('series_id')
})

test('a name fixed during the stream is sent, with the glossary save, and listed', async ({ page }) => {
  const m = await mockLive(page)
  const fixes: unknown[] = []
  await page.route(`**/api/live/sessions/${SID}/names`, (route) => {
    const body = route.request().postDataJSON() as { source: string; english: string }
    fixes.push(body)
    m.state.figures = { ...m.state.figures, name_fixes: [{ source: body.source, english: body.english }] }
    return route.fulfill({ json: { session_id: SID, fixes: [{ source: body.source, english: body.english }] } })
  })
  const live = await openLive(page)
  await live.getByLabel('Use glossary and packs from', { exact: true }).selectOption({ label: 'Series: Moonlit' })
  m.state.status = 'running'
  m.state.cues = [cue(1)]
  m.state.figures = { can_fix_names: true, has_glossary: true, name_fixes: [] }
  await start(live)
  await live.getByRole('button', { name: 'Fix a name' }).click()
  await live.getByLabel('Name as heard').fill('高市')
  await live.getByLabel('Right name in English').fill('Takaichi')
  await expect(live.getByLabel('Also save it to the glossary')).toBeChecked()
  await live.getByRole('button', { name: 'Fix', exact: true }).click()
  await expect(live.getByTestId('live-namefix-list')).toContainText('高市 → Takaichi')
  expect(fixes).toEqual([{ source: '高市', english: 'Takaichi', save_to_glossary: true }])
  expect(m.unmocked).toEqual([])
})
