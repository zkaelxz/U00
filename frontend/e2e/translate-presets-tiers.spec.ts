import { expect, test } from '@playwright/test'

// Parity X02 (apply a workflow tier) and X22 (save as preset). The two write
// routes are mocked so the shared seeded library is left as it was; config
// reads hit the real seeded API. pytest covers the real routes.

test('applying a tier fills the form and starts nothing', async ({ page }) => {
  const tierBodies: unknown[] = []
  const runs: unknown[] = []
  await page.route('**/api/translate-run/dramas/1/workflow-tier', async (route) => {
    tierBodies.push(route.request().postDataJSON())
    await route.fulfill({
      json: {
        drama_id: 1, tier: 'release', label: 'Release -- best quality, checked before export',
        translation_engine: 'claude', engine_model: 'claude-opus-4-8', reflect: true, auto_qc: true,
      },
    })
  })
  await page.route('**/api/translate-run/dramas/1/run', (route) => {
    runs.push(route.request().postDataJSON())
    return route.abort()
  })

  await page.goto('/#/drama/1/translate')
  const run = page.getByRole('region', { name: 'Translate run' })
  const tier = run.getByLabel('Starting tier', { exact: true })
  await expect(tier).toHaveValue('standard')
  await tier.selectOption('release')
  await run.getByRole('button', { name: 'Apply tier' }).click()
  await expect(run.getByRole('status')).toContainText('Nothing has started')
  expect(tierBodies).toEqual([{ tier: 'release' }])

  await expect(run.getByLabel('Engine', { exact: true })).toHaveValue('claude')
  await run.getByText('Advanced', { exact: true }).click()
  await expect(run.getByLabel('Reflect', { exact: true })).toBeChecked()
  expect(runs).toEqual([])
  await page.screenshot({ path: 'test-results/translate-tier-applied.png', fullPage: true })
})

test('save as preset asks for a name and confirms before replacing', async ({ page }) => {
  const bodies: Record<string, unknown>[] = []
  await page.route('**/api/translate-run/presets', async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>
    bodies.push(body)
    if (!body.overwrite) {
      await route.fulfill({ status: 409, json: { error: { code: 'conflict', message: 'taken' } } })
      return
    }
    await route.fulfill({
      json: {
        replaced: true,
        preset: {
          id: 1, name: body.name, translation_engine: body.translation_engine, engine_model: null,
          style_preset: body.style_preset, locale: body.locale, default_female_pronouns: 1,
          include_genre_notes: 1,
        },
      },
    })
  })

  await page.goto('/#/drama/1/translate')
  const run = page.getByRole('region', { name: 'Translate run' })
  await run.getByText('Advanced', { exact: true }).click()
  await run.getByLabel('Default ambiguous pronouns to she/her').check()
  await run.getByRole('button', { name: 'Save as preset…' }).click()

  await run.getByRole('button', { name: 'Save preset' }).click()
  await expect(run.getByRole('alert')).toContainText('Give the preset a name')
  expect(bodies).toEqual([])

  await run.getByLabel('Preset name', { exact: true }).fill('  My mix ')
  await run.getByRole('button', { name: 'Save preset' }).click()
  await expect(run.getByRole('alert')).toContainText('A preset named "My mix" already exists')
  expect(bodies[0]).toMatchObject({ name: 'My mix', default_female_pronouns: true, include_genre_notes: true })
  expect(bodies[0].overwrite).toBeUndefined()
  expect(typeof bodies[0].translation_engine).toBe('string')

  await run.getByRole('button', { name: 'Replace it' }).click()
  await expect(run.getByRole('status')).toContainText('Replaced preset "My mix"')
  expect(bodies[1]).toMatchObject({ name: 'My mix', overwrite: true })
  await page.screenshot({ path: 'test-results/translate-preset-saved.png', fullPage: true })
})
