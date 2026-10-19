import { expect, test } from '@playwright/test'
import { openSection } from './diagnosticsInstallsMocks'
import { openDiagTab } from './diagnosticsTabsNav'

import { guardWrites, mockDiagnostics } from './diagnosticsConsolidateMocks'
import { installHitArea } from './hitArea'
import { expectNoSidewaysScroll } from './mockHelpers'

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// Phone project (390x844, touch): the consolidated Diagnostics folds fit.

test('Setup with models, speaker detection and a task fit a phone with 44px targets', async ({ page }) => {
  const unmocked = await guardWrites(page)
  await mockDiagnostics(page, { ffmpegFound: false, gpuState: 'cpu_on_gpu' })
  await page.goto('/#/diagnostics')
  const fold = (title: RegExp) => page.locator('details.section', { has: page.locator('> summary', { hasText: title }) }).first()
  await expect(fold(/^Setup/)).toHaveJSProperty('open', true) // a problem
  await openDiagTab(page, 'Models')
  await openSection(page, /^Models/)
  await expect(fold(/^Models/).getByRole('list', { name: 'Whisper (faster-whisper) downloads' })).toBeVisible()
  await openDiagTab(page, 'Packages and installs')
  await openSection(page, /^Speaker detection/)
  await expect(page.getByRole('button', { name: 'Check access online' })).toBeVisible()
  await openSection(page, /^Packages/)
  await page.getByTestId('install-tasks').locator('details.section > summary').first().click()
  await page.getByTestId('task-details-hardsub_ocr').locator('summary').click()
  await expectNoSidewaysScroll(page)
  const small = await page.locator('button:not(.field-help-btn):not(.toggle), summary, a.btn').evaluateAll((els) =>
    els.filter((e) => {
      const r = e.getBoundingClientRect()
      return r.width > 0 && r.height > 0 && window.hitHeight(e) < 43.5
    }).map((e) => `${e.tagName} ${(e.textContent ?? '').trim().slice(0, 30)}`))
  expect(small).toEqual([])
  expect(unmocked).toEqual([])
})
