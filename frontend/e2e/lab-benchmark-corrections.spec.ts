import { expect, test } from './fixtures'
import { openBenchTab } from './benchSections'

// "Add a title's corrections to a test set" on the Golden sets tab: the title's stats show once it is
// picked, a preview counts without adding, and adding says unedited lines were skipped. The corrections
// routes are mocked (the seeded library has no raw transcript or audio to clip); the form is real.
const stats = { drama_id: 1, raw_available: true, lines_compared: 200, lines_corrected: 14, raw_to_edited_cer: 0.084 }
const added = (dry: boolean) => ({
  set_name: 'moon-fixes', lines_compared: 200, lines_corrected: 14, unedited_skipped: 186, unclippable_skipped: 0,
  case_count: 14, added: dry ? 0 : 14, skipped: 0, dry_run: dry,
})

for (const theme of ['light', 'dark', 'sepia', 'oled']) {
  test(`Benchmark Lab: add a title's corrections to a new test set (${theme})`, async ({ page }) => {
    await page.addInitScript((t) => localStorage.setItem('baihe.theme', t), theme)
    await page.route('**/api/benchmark/dramas/*/corrections', (r) => r.fulfill({ json: stats }))
    const bodies: unknown[] = []
    await page.route('**/api/benchmark/dramas/*/corrections/add', (r) => {
      const body = r.request().postDataJSON()
      bodies.push(body)
      return r.fulfill({ json: added(body.dry_run) })
    })
    await page.goto('/#/benchmark')
    await openBenchTab(page, 'Golden sets')
    const section = page.locator('details', { hasText: "Add a title's corrections to a test set" })
    await section.getByLabel('Title', { exact: true }).selectOption({ index: 1 })
    await expect(section.getByTestId('corrections-stats')).toHaveText(
      '14 of 200 lines corrected. The raw transcript was 8.4% off your text.')

    const add = section.getByRole('button', { name: 'Add lines' })
    await expect(add).toBeDisabled()
    await expect(section.getByTestId('corrections-reason')).toHaveText('Choose a set or give a new one a name.')
    await section.getByLabel('New set name').fill('moon-fixes')
    await expect(add).toBeEnabled()

    await section.getByRole('button', { name: 'Preview' }).click()
    await expect(section.getByTestId('corrections-result')).toHaveText(
      'This would add 14 clips to “moon-fixes”. 186 unedited lines skipped.')
    await add.click()
    await expect(section.getByTestId('corrections-result')).toHaveText(
      'Added 14 clips to “moon-fixes”. 186 unedited lines skipped.')
    expect(bodies).toEqual([{ set_name: 'moon-fixes', dry_run: true }, { set_name: 'moon-fixes', dry_run: false }])

    const dir = process.env.CORRECTIONS_SHOTS_DIR
    if (dir) await section.screenshot({ path: `${dir}/after-corrections-${theme}-desktop.png` })
  })
}

for (const theme of ['light', 'dark', 'sepia', 'oled']) {
test(`Benchmark Lab: include all lines of a title adds unedited lines with their current text as the reference (${theme})`, async ({ page }) => {
  await page.addInitScript((t) => localStorage.setItem('baihe.theme', t), theme)
  const none = { drama_id: 1, raw_available: false, lines_compared: 0, lines_corrected: 0, raw_to_edited_cer: null }
  await page.route('**/api/benchmark/dramas/*/corrections', (r) => r.fulfill({ json: none }))
  const bodies: unknown[] = []
  await page.route('**/api/benchmark/dramas/*/corrections/add', (r) => {
    const body = r.request().postDataJSON()
    bodies.push(body)
    return r.fulfill({
      json: {
        set_name: 'timed', lines_compared: 120, lines_corrected: 120, include_all: true, unedited_skipped: 0,
        unclippable_skipped: 0, case_count: 100, remaining: 20, added: body.dry_run ? 0 : 100, skipped: 0, dry_run: body.dry_run,
      },
    })
  })
  await page.goto('/#/benchmark')
  await openBenchTab(page, 'Golden sets')
  const section = page.locator('details', { hasText: "Add a title's corrections to a test set" })
  await section.getByLabel('Title', { exact: true }).selectOption({ index: 1 })
  await section.getByLabel('New set name').fill('timed')
  await expect(section.getByTestId('corrections-reason')).toHaveText('This title has no original transcription to compare with.')
  await section.getByRole('switch', { name: 'Include all lines of this title' }).click()
  await expect(section.getByTestId('corrections-include-all-note')).toBeVisible()
  await expect(section.getByRole('button', { name: 'Add lines' })).toBeEnabled()
  await section.getByRole('button', { name: 'Preview' }).click()
  await expect(section.getByTestId('corrections-result')).toContainText('Current text counts as the reference.')
  expect(bodies).toEqual([{ set_name: 'timed', dry_run: true, include_all: true }])

  const dir = process.env.CORRECTIONS_SHOTS_DIR
  if (dir) await section.screenshot({ path: `${dir}/after-include-all-${theme}-desktop.png` })
})
}
