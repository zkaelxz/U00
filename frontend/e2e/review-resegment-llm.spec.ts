import { expect, test } from '@playwright/test'

import { PREVIEW, clearLines, mockAiResegment, openAiStructure, seedLines } from './resegmentLlmMocks'

// Parity R47: the LLM re-segmentation as a preview in the Review stage's
// Structure section. Lines are real (seeded); the preview job, its GET, the
// apply and the job records are mocked.

test.beforeEach(() => seedLines())
test.afterAll(() => clearLines())

test('previews with AI, shows before → after, and Apply needs the typed confirm', async ({ page }) => {
  const calls = await mockAiResegment(page)
  const group = await openAiStructure(page)

  // Before starting: the cost and this month's spend; translation-only engines are not offered.
  await expect(group.getByTestId('resegment-ai-cost')).toHaveText(
    'A paid AI call, counted toward the monthly spending cap. Spent this month: $1.25 of $20.00.',
  )
  await group.locator('summary', { hasText: 'Advanced' }).click()
  const engine = group.getByRole('combobox', { name: 'Engine' })
  await expect(engine.locator('option').first()).toHaveText('Default (Claude)')
  await expect(engine.locator('option', { hasText: 'LibreTranslate' })).toHaveCount(0)
  await engine.selectOption('gemini')
  await group.getByRole('combobox', { name: 'Model' }).selectOption('flash')

  await group.getByRole('button', { name: 'Preview with AI' }).click()
  await expect.poll(() => calls.previewStarts).toEqual([{ engine: 'gemini', model: 'flash' }])
  await expect(page.getByTestId('job-status')).toContainText('done')

  const shown = page.getByTestId('resegment-ai-preview')
  await expect(shown).toContainText('3 → 5 lines · 2 lines split · by Gemini')
  const items = shown.getByRole('list', { name: 'Proposed splits' }).locator(':scope > li')
  await expect(items).toHaveCount(2)
  await expect(items.nth(0)).toContainText('#1')
  await expect(items.nth(0)).toContainText(PREVIEW.changed[0].zh)
  await expect(items.nth(0).getByRole('list').locator('li')).toHaveText(['你好我的朋友', '今天天气真的很好'])
  await expect(items.nth(1).getByRole('list').locator('li')).toHaveText(['我们一起去公园散步', '然后吃晚饭吧'])
  await expect(shown).toContainText('logged with this drama’s usage')
  await expect(shown).toContainText('1 translation and 1 flag on the lines being split will be dropped.')

  // Apply stays off until the word is typed, then sends only the preview's ids.
  const apply = shown.getByRole('button', { name: 'Apply' })
  await expect(apply).toBeDisabled()
  await shown.getByLabel('Type resegment to confirm').fill('resegment')
  await apply.click()
  await expect.poll(() => calls.applies).toEqual([{ expected_line_ids: [101, 102, 103], use_preview: true, confirm: true }])
  // The apply job finishes: the preview clears.
  await expect(page.getByTestId('resegment-ai-preview')).toHaveCount(0)
  await expect(page.getByTestId('job-status')).toContainText('done')
})

test('Discard drops the preview without writing anything', async ({ page }) => {
  const calls = await mockAiResegment(page)
  const group = await openAiStructure(page)
  await group.getByRole('button', { name: 'Preview with AI' }).click()
  await expect.poll(() => calls.previewStarts).toEqual([{}])
  const shown = page.getByTestId('resegment-ai-preview')
  await expect(shown).toContainText('3 → 5 lines')
  await shown.getByRole('button', { name: 'Discard' }).click()
  await expect(page.getByTestId('resegment-ai-preview')).toHaveCount(0)
  await expect(group.getByRole('button', { name: 'Preview with AI' })).toBeEnabled()
  expect(calls.applies).toEqual([])
})

test('a server refusal for want of confirm asks for it; lines changed says preview again', async ({ page }) => {
  let reply = 0
  const calls = await mockAiResegment(page, {
    preview: { ...PREVIEW, needs_confirm: false, translated: 0, flagged: 0 },
    apply: (route) => {
      reply += 1
      if (reply === 1) {
        return route.fulfill({ status: 422, json: { error: { code: 'validation_error', message: 'Re-segmenting would clear translations, flags or notes on the lines being split -- pass confirm=true.' } } })
      }
      return route.fulfill({ status: 409, json: { error: { code: 'conflict', message: "This drama's lines changed since the preview -- run the preview again." } } })
    },
  })
  const group = await openAiStructure(page)
  await group.getByRole('button', { name: 'Preview with AI' }).click()
  const shown = page.getByTestId('resegment-ai-preview')
  // No confirm needed at preview time: a plain Apply.
  await expect(shown.getByLabel('Type resegment to confirm')).toHaveCount(0)
  await shown.getByRole('button', { name: 'Apply' }).click()
  await expect(group.getByTestId('resegment-ai-notice')).toContainText('would be dropped')
  await expect(group.getByTestId('resegment-ai-notice')).not.toContainText('confirm=')
  await shown.getByLabel('Type resegment to confirm').fill('resegment')
  await shown.getByRole('button', { name: 'Apply' }).click()
  await expect(group.getByTestId('resegment-ai-notice')).toHaveText('The lines changed since this preview. Preview again.')
  await expect(page.getByTestId('resegment-ai-preview')).toHaveCount(0)
  // The server may still hold that preview: turning Use AI off and on doesn't bring it back.
  const useAi = group.getByRole('switch', { name: 'Use AI' })
  await useAi.click()
  await useAi.click()
  await expect(group.getByRole('button', { name: 'Preview with AI' })).toBeEnabled()
  await expect(page.getByTestId('resegment-ai-preview')).toHaveCount(0)
  expect(calls.applies).toEqual([
    { expected_line_ids: [101, 102, 103], use_preview: true, confirm: false },
    { expected_line_ids: [101, 102, 103], use_preview: true, confirm: true },
  ])
})

test('with Use AI off the rules preview still runs as before', async ({ page }) => {
  await page.route('**/api/restructure/dramas/3/resegment/preview', (route) => route.fulfill({
    json: { ...PREVIEW, engine: undefined, changed: [], line_count_after: 3 },
  }))
  await page.goto('/#/drama/3/review')
  await page.locator('.review-line:not(.review-skeleton)').first().waitFor()
  const group = page.getByRole('group', { name: 'Structure' })
  await group.locator('summary', { hasText: 'Structure' }).click()
  await group.getByRole('button', { name: 'Preview re-segmentation' }).click()
  // Nothing for the rules to split: the AI path is still one switch away.
  await expect(page.getByTestId('resegment-preview')).toContainText('Nothing to re-segment.')
  await expect(group.getByRole('switch', { name: 'Use AI' })).toBeEnabled()
})

test('a discarded preview stays discarded when Use AI is turned off and on again', async ({ page }) => {
  const calls = await mockAiResegment(page)
  const group = await openAiStructure(page)
  await group.getByRole('button', { name: 'Preview with AI' }).click()
  await expect(page.getByTestId('resegment-ai-preview')).toContainText('3 → 5 lines')
  await page.getByTestId('resegment-ai-preview').getByRole('button', { name: 'Discard' }).click()
  const useAi = group.getByRole('switch', { name: 'Use AI' })
  await useAi.click()
  await useAi.click()
  await expect(group.getByRole('button', { name: 'Preview with AI' })).toBeEnabled()
  await expect(page.getByTestId('resegment-ai-preview')).toHaveCount(0)
  expect(calls.applies).toEqual([])
})

test('a refusal the apply job finds at run time asks for the typed confirm', async ({ page }) => {
  const calls = await mockAiResegment(page, { preview: { ...PREVIEW, needs_confirm: false, translated: 0, flagged: 0 } })
  // Answers by apply count, not read count: the panel also reads this id on mount (reattach).
  await page.route('**/api/jobs/resegment_3', (route) => {
    const applied = calls.applies.length
    if (!applied) return route.fulfill({ status: 404, json: { error: { code: 'not_found', message: 'No job.' } } })
    return route.fulfill({
      json: {
        job_id: 'resegment_3', status: applied === 1 ? 'error' : 'done', progress: null, message: '', description: null,
        error: applied === 1 ? 'Re-segmenting would clear translations, flags or notes on the lines being split -- pass confirm=true.' : null,
        gpu_touching: false, started_at: 1, finished_at: 2, updated_at: 1,
      },
    })
  })
  const group = await openAiStructure(page)
  await group.getByRole('button', { name: 'Preview with AI' }).click()
  const shown = page.getByTestId('resegment-ai-preview')
  await shown.getByRole('button', { name: 'Apply' }).click()
  await expect(group.getByTestId('resegment-ai-notice')).toContainText('would be dropped')
  await shown.getByLabel('Type resegment to confirm').fill('resegment')
  await shown.getByRole('button', { name: 'Apply' }).click()
  await expect(page.getByTestId('resegment-ai-preview')).toHaveCount(0)
  expect(calls.applies.map((b) => b.confirm)).toEqual([false, true])
})

test('Preview with AI waits while another job runs on the drama (no paid call)', async ({ page }) => {
  const calls = await mockAiResegment(page)
  await page.route('**/api/jobs', (route) =>
    route.fulfill({ json: { items: [{ job_id: 'bulk_flag_3', status: 'running', progress: null, message: '', error: null, description: null, gpu_touching: false, started_at: 1, finished_at: null, updated_at: 1 }], count: 1 } }))
  const group = await openAiStructure(page)
  await expect(group.getByRole('button', { name: 'Preview with AI' })).toBeDisabled()
  await expect(group.getByTestId('resegment-ai-wait')).toContainText('A job is running on this drama.')
  expect(calls.previewStarts).toEqual([])
})
