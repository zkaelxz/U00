import type { Page } from '@playwright/test'

import { expect, test } from './fixtures'
import { openAi } from './reviewFolds'
import { gotoTranslate } from './translateTabs'

// The Translate button and the Review AI checks are off before the click when the engine they will
// call cannot run (no key, package missing, Ollama stopped), with the reason and the fix beside them.
// The run config is the real one with the engine state faked; no job is ever started.

type Tweak = (engines: Array<Record<string, unknown>>) => void

async function config(page: Page, engine: string, tweak: Tweak, ollama: boolean | null = null) {
  await page.route('**/api/translate-run/dramas/3/config', async (route) => {
    const res = await route.fetch()
    const cfg = await res.json()
    cfg.translation_engine = engine
    cfg.line_count = Math.max(cfg.line_count, 4)
    cfg.untranslated_count = Math.max(cfg.untranslated_count, 2)
    cfg.ollama_reachable = ollama
    tweak(cfg.engines)
    await route.fulfill({ response: res, json: cfg })
  })
}

const noKey = (name: string): Tweak => (engines) => {
  for (const e of engines) if (e.name === name) e.key_configured = false
}

test('Translate is off with an Add in Settings fix when the engine has no key', async ({ page }) => {
  await config(page, 'claude', noKey('claude'))
  await gotoTranslate(page, 'translate', 3)
  const go = page.getByRole('button', { name: /^Translate/ }).first()
  await expect(go).toBeDisabled()
  const gate = page.getByTestId('engine-gate')
  await expect(gate).toContainText('Claude has no API key')
  await expect(gate.getByRole('link', { name: 'Add in Settings' })).toBeVisible()
})

test('Translate names the missing package and links to Diagnostics', async ({ page }) => {
  await config(page, 'claude', (engines) => {
    for (const e of engines) if (e.name === 'claude') Object.assign(e, { key_configured: false, package_installed: false })
  })
  await gotoTranslate(page, 'translate', 3)
  await expect(page.getByTestId('engine-gate')).toContainText('anthropic package')
  await expect(page.getByTestId('engine-gate').getByRole('link', { name: 'Open Diagnostics' })).toBeVisible()
})

test('Translate stays on for an engine that can run', async ({ page }) => {
  await config(page, 'claude', () => undefined)
  await gotoTranslate(page, 'translate', 3)
  await expect(page.getByTestId('engine-gate')).toHaveCount(0)
})

test('Review AI checks are off, with the reason, when the title engine has no key', async ({ page }) => {
  await config(page, 'claude', noKey('claude'))
  await page.goto('/#/drama/3/review')
  const ai = await openAi(page)
  await expect(ai.getByRole('button', { name: 'Check consistency' })).toBeDisabled()
  await expect(ai.getByTestId('engine-gate').first()).toContainText('Claude has no API key')
})
