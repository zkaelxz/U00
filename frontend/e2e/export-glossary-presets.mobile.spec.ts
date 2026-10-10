import { expect, test, type Locator, type Page } from '@playwright/test'
import { hitHeight, installHitArea } from './hitArea'
import { openExportBlocks } from './exportBlocks'

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// Phone project (390x844, touch): the new Export video sections and Mark as
// exported, the Glossary import fold, the preset picker with style guidance,
// and the Library rename form fit the width with 44px touch targets. Lists
// are mocked; nothing is started, imported or renamed.

const SHOTS = process.env.SHOT_DIR

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true })
}

async function expectNoHorizontalOverflow(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

async function expectTall(loc: Locator) {
  await expect(loc.first()).toBeVisible()
  const n = await loc.count()
  for (let i = 0; i < n; i++) {
    expect(await hitHeight(loc.nth(i)), await loc.nth(i).innerText()).toBeGreaterThanOrEqual(44)
  }
}

// A Toggle is 24px tall with a 44px ::after hit area (as in settings.mobile.spec.ts).
async function expectSwitchTarget(loc: Locator) {
  await expect(loc.first()).toBeVisible()
  for (const sw of await loc.all()) {
    await sw.scrollIntoViewIfNeeded()
    const hit = await sw.evaluate((el) => {
      const r = el.getBoundingClientRect()
      const cx = r.left + r.width / 2
      const cy = r.top + r.height / 2
      const at = (y: number) => el.contains(document.elementFromPoint(cx, y))
      return { top: at(cy - 21), bottom: at(cy + 21), width: r.width }
    })
    expect(hit).toEqual({ top: true, bottom: true, width: 44 })
  }
}

const PRESETS = { items: [{ id: 7, name: 'A rather long preset name for a phone', translation_engine: 'claude', engine_model: null, style_preset: null, locale: null }] }

test('export video sections and Mark as exported on a phone', async ({ page }) => {
  await page.goto('/#/drama/1/export')
  await expectTall(page.getByRole('button', { name: 'Mark as exported' }))
  await page.getByText('Video and audio', { exact: true }).click()
  await openExportBlocks(page)
  const softsub = page.getByRole('group', { name: 'Video with a subtitle track' })
  const dubbed = page.getByRole('group', { name: 'Video with the dub audio' })
  await expectTall(softsub.getByRole('button'))
  await expectTall(softsub.getByLabel('Subtitles'))
  await expectTall(dubbed.getByRole('button'))
  await expectSwitchTarget(dubbed.getByRole('switch'))
  await expectNoHorizontalOverflow(page)
  await dubbed.scrollIntoViewIfNeeded()
  await shot(page, 'export-video-phone')
})

test('glossary import and preset picker on a phone', async ({ page }) => {
  await page.route('**/api/library/presets', (r) => r.fulfill({ json: PRESETS }))
  await page.route('**/api/glossary/dramas/1/terms', (r) => r.fulfill({ json: [
    { id: 1, term_original: '师姐', term_translation: 'Senior Sister', notes: '', category: null, policy: null, enforce_exact: false, aliases: [], banned_translations: [] },
  ] }))
  await page.goto('/#/drama/1/translate')
  await expectTall(page.getByRole('button', { name: 'Apply', exact: true }))
  await expectTall(page.getByLabel('Start from…', { exact: true }))
  await page.getByText('What this style asks the translator for').click()
  await expect(page.getByTestId('style-guidance')).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await shot(page, 'translate-preset-phone')

  const glossary = page.getByRole('region', { name: 'Glossary' })
  await glossary.getByText('Import or export').click()
  await expectTall(glossary.getByRole('button', { name: 'Import', exact: true }))
  await expectTall(glossary.getByRole('link', { name: 'Download glossary as CSV' }))
  await expectSwitchTarget(glossary.locator('.glossary-import').getByRole('switch'))
  await expectNoHorizontalOverflow(page)
  await glossary.getByText('Import or export').scrollIntoViewIfNeeded()
  await shot(page, 'glossary-import-phone')
})

test('library rename form on a phone', async ({ page }) => {
  await page.route('**/api/library/presets', (r) => r.fulfill({ json: PRESETS }))
  await page.goto('/#/library-tools')
  await page.locator('summary', { hasText: 'Presets' }).click()
  await expectTall(page.getByRole('button', { name: /^Rename / }))
  await page.getByRole('button', { name: /^Rename / }).click()
  await expectTall(page.getByRole('button', { name: 'Save name' }))
  await expectTall(page.getByRole('button', { name: 'Cancel' }))
  await expectNoHorizontalOverflow(page)
  await page.getByRole('button', { name: 'Save name' }).scrollIntoViewIfNeeded()
  await shot(page, 'library-rename-phone')
})
