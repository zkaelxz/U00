import { expect, test, type Page } from '@playwright/test'

import { mockDependencyInstall } from './dependencyInstallMock'
import { PENDING_BASE, planFor } from './pendingInstallMocks'
import { mockTranscription } from './transcriptionMissingMocks'

// The Transcribe tab's inline Whisper install (PreflightCard) asks for the same
// preview as Diagnostics > Packages, and its GPU banner. Shared by the desktop and
// phone specs. Every call is mocked: a catch-all refuses any other non-GET /api call.

const RISK = 'numpy 2.5.3 -> 2.3.5 is a downgrade of a package Baihe itself needs.'
const RISKY = planFor(['faster_whisper'], {
  changes: [{ name: 'numpy', from_version: '2.5.3', to_version: '2.3.5', kind: 'downgrade' }],
  summary: ['Install faster_whisper 1.2.0.', 'Change numpy 2.5.3 -> 2.3.5 (downgrade).'],
  needs_confirm: [RISK],
})

type Posted = { path: string; body: unknown }

async function openTranscribe(page: Page, plan: unknown) {
  const { sent } = await mockTranscription(page, false)
  const posted: Posted[] = []
  await page.route((u) => u.pathname === `${PENDING_BASE}/plan`, (r) => r.fulfill({ json: plan }))
  await page.route((u) => u.pathname === `${PENDING_BASE}/queue`, (r) => {
    posted.push({ path: new URL(r.request().url()).pathname, body: r.request().postDataJSON() })
    return r.fulfill({ json: { queued: true, install_now: false, plan } })
  })
  const install = await mockDependencyInstall(page, () => ({ ok: true, output_tail: [] }))
  await page.goto('/#/drama/1/source?tab=transcribe')
  const card = page.getByRole('region', { name: 'Before you run' })
  await card.getByRole('button', { name: /Install \(about 80 MB\)/ }).click()
  await card.getByRole('button', { name: /Confirm install/ }).click()
  return { sent, posted, install, panel: page.getByTestId('install-plan') }
}

export function registerInstallPlanCallerCases(prefix: string) {
  test.afterEach(async ({ page }) => {
    await page.unrouteAll({ behavior: 'ignoreErrors' })
  })

  test(`${prefix}Whisper install shows the risk and sends it only after the tick`, async ({ page }) => {
    const { install, panel, sent } = await openTranscribe(page, RISKY)
    await expect(panel).toContainText('Change numpy 2.5.3 -> 2.3.5 (downgrade).')
    await expect(panel).toContainText(RISK)
    const now = panel.getByRole('button', { name: 'Install now' })
    await expect(now).toBeDisabled()
    expect(install.started).toHaveLength(0)
    await panel.getByLabel('I understand, install anyway').check()
    await now.click()
    await expect.poll(() => install.started.length).toBe(1)
    expect(new URL(install.started[0].url()).pathname).toBe('/api/diagnostics/dependencies/faster_whisper/install')
    expect(install.started[0].postDataJSON()).toMatchObject({ accept_risk: true, accepted_risks: [RISK] })
    expect(sent).toEqual([])
  })

  test(`${prefix}Whisper install on a blocked plan never posts the install`, async ({ page }) => {
    const { install, panel, posted } = await openTranscribe(page, planFor(['faster_whisper'], {
      blocked: ['This would put two OpenCV packages side by side.'],
    }))
    await expect(panel).toContainText('two OpenCV packages')
    await expect(panel.getByRole('button', { name: 'Install now' })).toHaveCount(0)
    await panel.getByRole('button', { name: 'Close' }).click()
    await expect(panel).toHaveCount(0)
    expect(install.started).toHaveLength(0)
    expect(posted).toEqual([])
  })

  test(`${prefix}Whisper install that has to wait queues for restart with the accepted risk`, async ({ page }) => {
    const { install, panel, posted } = await openTranscribe(page, { ...RISKY, mode: 'restart', note: 'Baihe is using numpy right now.' })
    await expect(panel).toContainText('Installs when you restart Baihe.')
    const queue = panel.getByRole('button', { name: 'Install when I restart Baihe' })
    await expect(queue).toBeDisabled()
    await panel.getByLabel('I understand, install anyway').check()
    await queue.click()
    await expect.poll(() => posted.length).toBe(1)
    expect(posted[0].body).toEqual({ packages: ['faster_whisper'], confirm: true, accept_risk: true })
    await expect(page.getByRole('region', { name: 'Before you run' })).toContainText('transcription is queued')
    expect(install.started).toHaveLength(0)
  })

  test(`${prefix}Whisper install shows the OpenCV removal line`, async ({ page }) => {
    const swap = "Remove opencv-python first: PaddleOCR needs opencv-contrib-python, and the two cannot share one 'cv2' folder."
    const { panel } = await openTranscribe(page, planFor(['faster_whisper'], { summary: [swap], mode: 'restart' }))
    await expect(panel).toContainText('Remove opencv-python first')
  })
}
