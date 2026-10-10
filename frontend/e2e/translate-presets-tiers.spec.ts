import { expect, test } from '@playwright/test'

import { withTranslateLines } from './stageLineMocks'
import { engineShortName } from '../src/api/translate'

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
        translation_engine: 'claude', engine_model: 'claude-opus-5-5', reflect: true, auto_qc: true,
      },
    })
  })
  await page.route('**/api/translate-run/dramas/1/run', (route) => {
    runs.push(route.request().postDataJSON())
    return route.abort()
  })

  await page.goto('/#/drama/1/translate')
  const run = page.getByRole('region', { name: 'Translate run' })
  const tier = run.getByLabel('Start from…', { exact: true })
  await expect(tier).toHaveValue('tier:standard')
  await tier.selectOption('tier:release')
  await run.getByRole('button', { name: 'Apply', exact: true }).click()
  await expect(run.getByRole('status')).toContainText('Nothing has started')
  await expect(run.getByRole('status')).toContainText('Release recommends Auto QC; run it from the Export stage.')
  expect(tierBodies).toEqual([{ tier: 'release' }])

  await expect(run.getByLabel('AI engine', { exact: true })).toHaveValue('claude')
  await run.getByText('More options', { exact: true }).click()
  await expect(run.getByRole('switch', { name: 'Reflect', exact: true })).toBeChecked()
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
  await run.getByText('More options', { exact: true }).click()
  await run.getByRole('switch', { name: 'Default ambiguous pronouns to she/her' }).click()
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

test('after a tier, Default runs and saves with the new engine', async ({ page }) => {
  type Tier = { key: string; translation_engine: string; reflect: boolean }
  type Engine = { name: string; label: string; key_configured: boolean }
  const config = await (await page.request.get('/api/translate-run/dramas/1/config')).json()
  // A tier whose engine differs from the drama's current default.
  const tier: Tier = config.workflow_tiers.find(
    (t: Tier) => t.translation_engine !== config.translation_engine && !t.reflect)
  const eng: Engine = config.engines.find((e: Engine) => e.name === tier.translation_engine)
  await page.route('**/api/translate-run/dramas/1/workflow-tier', (route) =>
    route.fulfill({ json: { ...tier, drama_id: 1, tier: tier.key } }))
  const runs: Record<string, unknown>[] = []
  await page.route('**/api/translate-run/dramas/1/run', async (route) => {
    runs.push(route.request().postDataJSON())
    await route.fulfill({ status: 409, json: { error: { code: 'conflict', message: 'busy' } } })
  })
  const presets: Record<string, unknown>[] = []
  await page.route('**/api/translate-run/presets', async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>
    presets.push(body)
    await route.fulfill({
      json: {
        replaced: false,
        preset: {
          id: 9, name: body.name, translation_engine: body.translation_engine, engine_model: null,
          style_preset: null, locale: null, default_female_pronouns: 0, include_genre_notes: 1,
        },
      },
    })
  })

  await withTranslateLines(page)
  await page.goto('/#/drama/1/translate')
  const run = page.getByRole('region', { name: 'Translate run' })
  await run.getByLabel('Start from…', { exact: true }).selectOption(`tier:${tier.key}`)
  await run.getByRole('button', { name: 'Apply', exact: true }).click()
  await expect(run.getByRole('status')).toContainText('Nothing has started')
  const engine = run.getByLabel('AI engine', { exact: true })
  await engine.selectOption('')
  await expect(engine.locator('option[value=""]')).toHaveText(
    `Default (${engineShortName(eng)}${eng.key_configured ? '' : ' (no key)'})`)

  await run.getByRole('button', { name: /^Translate \d+ lines?$/ }).click()
  await expect.poll(() => runs.length).toBe(1)
  expect(runs[0].engine).toBeUndefined() // the server's (new) default engine

  await run.getByText('More options', { exact: true }).click()
  await run.getByRole('button', { name: 'Save as preset…' }).click()
  await run.getByLabel('Preset name', { exact: true }).fill('After tier')
  await run.getByRole('button', { name: 'Save preset' }).click()
  await expect.poll(() => presets.length).toBe(1)
  expect(presets[0].translation_engine).toBe(tier.translation_engine)
})

test('the she/her and genre toggles are saved for the title when changed', async ({ page }) => {
  const saved: Record<string, unknown>[] = []
  await page.route('**/api/dramas/1/metadata', async (route) => {
    saved.push(route.request().postDataJSON() as Record<string, unknown>)
    await route.fulfill({ json: {} })
  })
  await page.goto('/#/drama/1/translate')
  const run = page.getByRole('region', { name: 'Translate run' })
  await run.getByText('More options', { exact: true }).click()
  await run.getByRole('switch', { name: 'Default ambiguous pronouns to she/her' }).click()
  await expect(run.getByTestId('toggle-saved')).toHaveText('Saved for this title; every later run uses it.')
  await run.getByRole('switch', { name: 'Include baihe/GL genre guidance' }).click()
  await expect.poll(() => saved).toEqual([{ default_female_pronouns: false }, { include_genre_notes: false }])
})
