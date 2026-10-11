import { expect, test } from '@playwright/test'

import { mockLive, openLive } from './liveMocks'

// Live > "Use glossary and packs from title": the choice rides along with Start, is
// remembered per browser (live.options), and a remembered title that is gone is not sent.
// liveMocks.ts answers /api/library/dramas with the one title "Moonlit Letters" (id 7).

const URL_FIELD = 'https://www.youtube.com/watch?v=abc'
const OPTIONS_KEY = 'baihe.pref.live.options'

async function start(live: Awaited<ReturnType<typeof openLive>>) {
  await live.getByLabel('Stream link', { exact: true }).fill(URL_FIELD)
  await live.getByRole('button', { name: 'Start', exact: true }).click()
}

test('the picker lists the library titles after None', async ({ page }) => {
  await mockLive(page)
  const live = await openLive(page)
  const picker = live.getByLabel('Use glossary and packs from title', { exact: true })
  await expect(picker.locator('option')).toHaveText(['None', 'Moonlit Letters'])
  await expect(picker).toHaveValue('')
})

test('None starts with the old request, without glossary_drama_id', async ({ page }) => {
  const m = await mockLive(page)
  const live = await openLive(page)
  const picker = live.getByLabel('Use glossary and packs from title', { exact: true })
  await picker.selectOption({ label: 'Moonlit Letters' })
  await picker.selectOption({ label: 'None' })
  await start(live)
  await expect.poll(() => m.posts.length).toBe(1)
  expect(m.posts[0].body).not.toHaveProperty('glossary_drama_id')
  expect(m.unmocked).toEqual([])
})

test('the chosen title is remembered across a reload', async ({ page }) => {
  const m = await mockLive(page)
  const live = await openLive(page)
  await live.getByLabel('Use glossary and packs from title', { exact: true }).selectOption({ label: 'Moonlit Letters' })
  await expect.poll(() => page.evaluate((k) => localStorage.getItem(k), OPTIONS_KEY)).toContain('"glossary_drama_id":7')

  await page.reload()
  const again = page.getByRole('region', { name: 'Live' })
  await expect(again.getByLabel('Use glossary and packs from title', { exact: true })).toHaveValue('7')
  await start(again)
  await expect.poll(() => m.posts.length).toBe(1)
  expect(m.posts[0].body).toMatchObject({ glossary_drama_id: 7 })
})

test('a remembered title that is no longer in the list is shown as None and not sent', async ({ page }) => {
  await page.addInitScript(([k]) => localStorage.setItem(k, JSON.stringify({ glossary_drama_id: 99 })), [OPTIONS_KEY])
  const m = await mockLive(page)
  const live = await openLive(page)
  const picker = live.getByLabel('Use glossary and packs from title', { exact: true })
  await expect(picker.locator('option')).toHaveCount(2)
  await expect(picker).toHaveValue('')
  await start(live)
  await expect.poll(() => m.posts.length).toBe(1)
  expect(m.posts[0].body).not.toHaveProperty('glossary_drama_id')
})
