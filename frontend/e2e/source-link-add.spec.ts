import { expect, test, type Page } from '@playwright/test'

import { selectMediaTab } from './mediaTabs'

// "Add a source link" sits in the title header of every title without a link and points at Details. It used to
// do nothing when Details was already open (the same address) or land with the Edit details card folded and the
// field off screen. Seeded title 1 is an audio drama, served here without a link.

const ID = 1
const FIELD = 'Source link (where you got it)'
const field = (page: Page) => page.getByLabel(FIELD, { exact: true })

// Simple's title row keeps the link behind More.
async function addLink(page: Page, view: 'simple' | 'full') {
  if (view === 'simple') {
    const more = page.getByRole('button', { name: 'More', exact: true })
    if ((await more.getAttribute('aria-expanded')) !== 'true') await more.click()
  }
  return page.getByRole('link', { name: 'Add a source link' })
}

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

async function withoutLink(page: Page, view: 'simple' | 'full') {
  const drama = await (await page.request.get(`/api/library/dramas/${ID}`)).json()
  await page.route(`**/api/library/dramas/${ID}`, (route) => route.fulfill({ json: { ...drama, source_url: null } }))
  await page.addInitScript((v) => {
    localStorage.setItem('baihe.pref.title.view', JSON.stringify(v))
    // The card is remembered as folded, the way a person who closed it once finds it.
    localStorage.setItem('baihe.section.source.details', '0')
  }, view)
}

for (const view of ['simple', 'full'] as const) {
  test(`${view} view: Add a source link opens Edit details at the link field, also when pressed again on Details`, async ({ page }) => {
    await withoutLink(page, view)
    await page.goto(`/#/drama/${ID}/source?tab=details`)
    await expect(await addLink(page, view)).toBeVisible()
    await expect(field(page)).toBeHidden()

    await (await addLink(page, view)).click()
    await expect(field(page)).toBeVisible()
    await expect(field(page)).toBeFocused()
    await expect(field(page)).toBeInViewport()

    // Already on Details: the address does not change, so the second press must still bring the field back.
    await page.evaluate(() => window.scrollTo(0, 0))
    await expect(field(page)).not.toBeInViewport()
    await (await addLink(page, view)).click()
    await expect(field(page)).toBeFocused()
    await expect(field(page)).toBeInViewport()

    // Another tab keeps `focus=sourceLink` in the address without a route change, so the press must still switch
    // back to Details and bring the field.
    await selectMediaTab(page, 'transcribe')
    await expect(field(page)).toBeHidden()
    await (await addLink(page, view)).click()
    await expect(field(page)).toBeFocused()
    await expect(field(page)).toBeInViewport()
  })
}

test('simple view: the title page link lands on Details with the link field ready to type in', async ({ page }) => {
  await withoutLink(page, 'simple')
  await page.goto(`/#/drama/${ID}`)
  await (await addLink(page, 'simple')).click()
  await expect(field(page)).toBeFocused()
  await expect(field(page)).toBeInViewport()
})
