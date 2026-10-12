import { expect, type Locator, type Page } from '@playwright/test'
import { openAdvanced } from './sourceHelpers'

type State = 'not-downloaded' | 'failed' | 'downloaded' | 'standard'

const BASE = {
  qwen_asr_batch_size: 1, qwen_asr_batch_min: 1, qwen_asr_batch_max: 16, qwen_asr_version: null,
  qwen_asr_batching_available: false, qwen_vad_refine_timing: false, mixed_languages: false,
  voice_detector: 'auto', asmr_vad_onnxruntime_installed: true, asmr_vad_model_downloaded: false,
  asmr_vad_download_job_id: 'job-vad',
}

export async function openVoiceDetector(page: Page, state: State) {
  const options = {
    ...BASE,
    voice_detector: state === 'standard' ? 'standard' : 'auto',
    asmr_vad_model_downloaded: state === 'downloaded',
  }
  await page.route('**/api/settings/asr-options', (route) => route.fulfill({ json: options }))
  await page.route('**/api/jobs/job-vad', (route) =>
    route.fulfill({
      json: state === 'failed'
        ? { id: 'job-vad', status: 'error', error: 'The ASMR detector download failed. Check your connection and try again.' }
        : { id: 'job-vad', status: 'done' },
    }))
  await page.goto('/#/drama/1/source')
  await openAdvanced(page)
  const select = page.getByLabel('Voice detector', { exact: true })
  await expect(select).toBeVisible()
  return select
}

// The select takes the cell's width, and the notes and button stack below it inside the cell.
export async function expectStackedLayout(page: Page, state: State, maxHeight: number) {
  const select = await openVoiceDetector(page, state)
  const cell = page.locator('.field-with-extras')
  const cellBox = (await cell.boundingBox())!
  const selectBox = (await select.boundingBox())!
  expect(selectBox.width, 'select fills the cell').toBeGreaterThan(cellBox.width * 0.9)
  expect(cellBox.height, 'field height').toBeLessThan(maxHeight)

  const below = state === 'failed'
    ? [page.getByTestId('voice-detector-download-problem')]
    : state === 'not-downloaded'
      ? [page.getByTestId('voice-detector-note'), page.getByRole('button', { name: /Download the ASMR detector/ })]
      : []
  for (const el of below) {
    await expect(el).toBeVisible()
    const box = (await el.boundingBox())!
    expect(box.y, 'sits below the select').toBeGreaterThanOrEqual(selectBox.y + selectBox.height - 1)
    expect(box.x + box.width, 'inside the cell').toBeLessThanOrEqual(cellBox.x + cellBox.width + 1)
  }
  if (state === 'not-downloaded') {
    expect((await below[0].boundingBox())!.width, 'status text is not squeezed').toBeGreaterThan(160)
  }
  if (state === 'standard' || state === 'downloaded') {
    await expect(page.getByTestId('voice-detector-note')).toHaveCount(0)
    await expect(page.getByRole('button', { name: /Download the ASMR detector/ })).toHaveCount(0)
  }
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

// A tall neighbour (the Voice detector's note and button) once stretched the whole grid row, so every other
// field's input dropped to the bottom of its cell, away from its label.
export async function expectInputsBesideLabels(page: Page) {
  const gaps = await page.locator('.source-grid > .field-item, .source-grid > .field-with-extras > .field-item').evaluateAll((items) =>
    items.map((item) => {
      const label = item.querySelector('label')!.getBoundingClientRect()
      const control = item.querySelector('.field-control')!.getBoundingClientRect()
      return { name: item.querySelector('label')!.textContent, gap: control.top - label.bottom }
    }))
  expect(gaps.length, 'fields in the grid').toBeGreaterThan(4)
  for (const { name, gap } of gaps) {
    expect(gap, `${name}: input sits right under its label`).toBeLessThan(12)
  }
}

// The closed select must show its whole selected text, or cut it with an ellipsis rather than mid-letter.
export async function expectSelectTextReadable(select: Locator) {
  const style = await select.evaluate((el) => {
    const s = getComputedStyle(el)
    return { overflow: s.textOverflow }
  })
  expect(style.overflow, 'long selected text ends in an ellipsis').toBe('ellipsis')
}

// A label that wraps once left its input lower than its neighbours' in the same row, away from their tops.
// Items in one visual row share a top; their inputs must too, and a unit suffix stays inside its field.
export async function expectRowInputsAligned(scope: Locator) {
  const rows = await scope.locator('.source-grid').evaluateAll((grids) =>
    grids.flatMap((grid) => {
      const cells = [...grid.querySelectorAll<HTMLElement>(':scope > .field-item, :scope > .field-with-extras > .field-item')]
      return cells.map((item) => {
        const control = item.querySelector<HTMLElement>('.field-control')!
        const input = control.firstElementChild!.getBoundingClientRect()
        const unit = control.querySelector<HTMLElement>('.field-unit')?.getBoundingClientRect()
        return {
          name: item.querySelector('label')!.textContent ?? '',
          row: Math.round((item.closest('.field-with-extras') ?? item).getBoundingClientRect().top),
          inputTop: input.top,
          fieldRight: item.getBoundingClientRect().right,
          unitRight: unit?.right ?? null,
        }
      })
    }))
  expect(rows.length, 'fields in the grids').toBeGreaterThan(3)
  const byRow = new Map<number, typeof rows>()
  for (const r of rows) {
    const key = [...byRow.keys()].find((k) => Math.abs(k - r.row) <= 2) ?? r.row
    byRow.set(key, [...(byRow.get(key) ?? []), r])
  }
  for (const group of byRow.values()) {
    const top = group[0].inputTop
    for (const r of group) expect(Math.abs(r.inputTop - top), `${r.name}: input top matches its row`).toBeLessThanOrEqual(1)
  }
  for (const r of rows) if (r.unitRight !== null) expect(r.unitRight, `${r.name}: unit stays inside the field`).toBeLessThanOrEqual(r.fieldRight + 0.5)
}
