import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { PendingInstallPlan, PendingInstallStatus } from '../../types/pendingInstall'
import { InstallPlanPanel, PendingInstallBanner, needsPlanPanel, pendingKeys } from './PendingInstall'

const plan = (o: Partial<PendingInstallPlan> = {}): PendingInstallPlan => ({
  packages: ['paddleocr'], available: true, mode: 'now', changes: [], summary: [], loaded: [], blocked: [],
  needs_confirm: [], note: null, ...o,
})

const noop = () => undefined
const panel = (p: PendingInstallPlan) => renderToStaticMarkup(
  createElement(InstallPlanPanel, { planned: { label: 'PaddleOCR', keys: ['paddleocr'], plan: p, installNow: noop }, onQueue: noop, onCancel: noop }),
)
const banner = (s: PendingInstallStatus) =>
  renderToStaticMarkup(createElement(PendingInstallBanner, { status: s, onCancel: noop, onDismiss: noop }))

describe('needsPlanPanel', () => {
  it('lets a plain fresh install go straight ahead', () => {
    expect(needsPlanPanel(plan({ changes: [{ name: 'paddleocr', from_version: null, to_version: '3.2.0', kind: 'install' }] }))).toBe(false)
  })

  it.each([
    ['a replaced package', { changes: [{ name: 'numpy', from_version: '2.5.3', to_version: '2.6.0', kind: 'upgrade' as const }] }],
    ['a wait for restart', { mode: 'restart' as const }],
    ['a risk to accept', { needs_confirm: ['numpy is a downgrade.'] }],
    ['a refusal', { blocked: ['two OpenCV packages'] }],
  ])('shows the panel for %s', (_name, o) => {
    expect(needsPlanPanel(plan(o))).toBe(true)
  })
})

describe('InstallPlanPanel', () => {
  it('lists what changes and says it installs when Baihe restarts, without closing anything itself', () => {
    const html = panel(plan({
      mode: 'restart', summary: ['Install paddleocr 3.2.0.', 'Change numpy 2.5.3 -> 2.3.5 (downgrade).'],
      note: "Baihe is using numpy right now, and Windows can't replace files in use.",
    }))
    expect(html).toContain('Install paddleocr 3.2.0.')
    expect(html).toContain('numpy 2.5.3 -&gt; 2.3.5')
    expect(html).toContain('Installs when you restart Baihe.')
    expect(html).toContain('Nothing is closed for you.')
    expect(html).toContain('Install when I restart Baihe')
  })

  it('holds the button until a risk is accepted', () => {
    const html = panel(plan({ mode: 'restart', needs_confirm: ['numpy 2.5.3 -> 2.3.5 is a downgrade of a package Baihe itself needs.'] }))
    expect(html).toContain('I understand, install anyway')
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Install when I restart Baihe/)
  })

  it('offers only Close for a refused plan, with the plain reason', () => {
    const html = panel(plan({ mode: 'restart', blocked: ['This would put two OpenCV packages side by side.'] }))
    expect(html).toContain('two OpenCV packages')
    expect(html).toContain('Close')
    expect(html).not.toContain('Install when I restart')
    expect(html).not.toContain('Install now')
  })

  it('offers Install now when nothing is in use', () => {
    expect(panel(plan({ summary: ['Change numpy 2.5.3 -> 2.6.0.'] }))).toContain('Install now')
  })
})

const status = (o: Partial<PendingInstallStatus> = {}): PendingInstallStatus => ({
  packages: [], created: null, before: {}, problem: null, result: null, applying: false, ...o,
})

describe('PendingInstallBanner', () => {
  it('shows the queued install with Cancel', () => {
    const html = banner(status({ packages: ['paddleocr', 'paddlepaddle'] }))
    expect(html).toContain('pending install (restart)')
    expect(html).toContain('paddleocr, paddlepaddle will install when you restart Baihe')
    expect(html).toContain('Cancel the queued install')
  })

  it('says how the last start-up install went in plain words, with Dismiss', () => {
    const html = banner(status({
      result: {
        status: 'failed', packages: ['paddleocr'], message: 'The install did not work. Your earlier packages were put back.',
        tail: ['ERROR: no matching distribution'], restored: ['numpy'], restore_failed: [], finished: 1,
      },
    }))
    expect(html).toContain('Your earlier packages were put back.')
    expect(html).toContain('no matching distribution')
    expect(html).toContain('Dismiss')
    expect(html).not.toContain('Cancel the queued install')
  })

  it('shows a refused file and nothing else when nothing is queued', () => {
    const html = banner(status({ problem: 'The queued install file was changed after Baihe wrote it, so it was not run.' }))
    expect(html).toContain('changed after Baihe wrote it')
    expect(html).not.toContain('pending install (restart)')
  })
})

describe('pendingKeys', () => {
  it('is empty without a status', () => {
    expect(pendingKeys(null).size).toBe(0)
    expect(pendingKeys(status({ packages: ['cv2'] })).has('cv2')).toBe(true)
  })
})
