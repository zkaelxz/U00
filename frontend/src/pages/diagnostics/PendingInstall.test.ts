import { createElement, isValidElement, type ReactElement, type ReactNode } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { PendingInstallPlan, PendingInstallStatus } from '../../types/pendingInstall'
import { ApiError } from '../../api/client'
import {
  InstallPlanPanel, PendingInstallBanner, needsPlanPanel, pendingKeys, planCheckUnavailable, planPanelKey,
} from './PendingInstall'

const plan = (o: Partial<PendingInstallPlan> = {}): PendingInstallPlan => ({
  packages: ['paddleocr'], available: true, mode: 'now', changes: [], summary: [], loaded: [], blocked: [],
  needs_confirm: [], note: null, ...o,
})

// The suite has no DOM, so the panel's one piece of state (the "I understand"
// tick) is held here: a test renders the panel as a plain element tree, fires
// the real onChange/onClick handlers and renders again, as React would.
const tick = vi.hoisted(() => ({ value: undefined as unknown }))
vi.mock('react', async (importOriginal) => {
  const real = await importOriginal<typeof import('react')>()
  return {
    ...real,
    useState: (init: unknown) => [tick.value === undefined ? init : tick.value, (v: unknown) => { tick.value = v }],
  }
})
beforeEach(() => { tick.value = undefined })

type Props = Record<string, unknown> & { children?: ReactNode }
function find(node: ReactNode, match: (type: unknown, props: Props) => boolean): ReactElement<Props> | null {
  if (Array.isArray(node)) {
    for (const n of node) {
      const hit = find(n, match)
      if (hit) return hit
    }
    return null
  }
  if (!isValidElement<Props>(node)) return null
  if (match(node.type, node.props)) return node
  return find(node.props.children, match)
}
const text = (node: ReactNode): string => (Array.isArray(node) ? node.map(text).join('')
  : isValidElement<Props>(node) ? text(node.props.children) : typeof node === 'string' ? node : '')

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

  it('sends the accepted risk with Install now only after the tick', () => {
    const risk = 'numpy 2.5.3 -> 2.3.5 is a downgrade of a package Baihe itself needs.'
    const installNow = vi.fn()
    const p = plan({ needs_confirm: [risk] })
    const render = () => (InstallPlanPanel as (props: unknown) => ReactElement)({
      planned: { label: 'PaddleOCR', keys: ['paddleocr'], plan: p, installNow }, onQueue: noop, onCancel: noop,
    })
    const button = () => find(render(), (t, props) => t === 'button' && text(props.children) === 'Install now')!
    expect(button().props.disabled).toBe(true)
    const box = find(render(), (t, props) => t === 'input' && props.type === 'checkbox')!
    ;(box.props.onChange as (e: unknown) => void)({ target: { checked: true } })
    expect(button().props.disabled).toBe(false)
    ;(button().props.onClick as () => void)()
    expect(installNow).toHaveBeenCalledWith([risk])
  })

  it('sends no accepted risk with Install now when there was none to accept', () => {
    const installNow = vi.fn()
    const el = (InstallPlanPanel as (props: unknown) => ReactElement)({
      planned: { label: 'PaddleOCR', keys: ['paddleocr'], plan: plan({ summary: ['Change numpy 2.5.3 -> 2.6.0.'] }), installNow },
      onQueue: noop, onCancel: noop,
    })
    ;(find(el, (t, props) => t === 'button' && text(props.children) === 'Install now')!.props.onClick as () => void)()
    expect(installNow).toHaveBeenCalledWith([])
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

describe('planCheckUnavailable', () => {
  const err = (status: number, code: string) => new ApiError(status, { code, message: code })

  it('skips the preview only for a missing endpoint or no answer', () => {
    expect(planCheckUnavailable(err(404, 'not_found'))).toBe(true)
    expect(planCheckUnavailable(err(0, 'network_error'))).toBe(true)
    expect(planCheckUnavailable(new TypeError('Failed to fetch'))).toBe(true)
  })

  it.each([
    ['a running job (409)', err(409, 'conflict')],
    ['a dry-run timeout (504)', err(504, 'timeout')],
    ['a refusal (403)', err(403, 'forbidden')],
    ['a server error (500)', err(500, 'internal_error')],
  ])('does not install on %s', (_name, e) => {
    expect(planCheckUnavailable(e)).toBe(false)
  })
})

describe('InstallPlanPanel queueing', () => {
  it('disables the queue button while the request is in flight', () => {
    const html = renderToStaticMarkup(createElement(InstallPlanPanel, {
      planned: { label: 'PaddleOCR', keys: ['paddleocr'], plan: plan({ mode: 'restart' }), installNow: noop },
      queueing: true, onQueue: noop, onCancel: noop,
    }))
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Install when I restart Baihe/)
  })
})

describe('planPanelKey', () => {
  it('differs per package set, so a tick for one set never carries to another', () => {
    expect(planPanelKey(['numpy'])).not.toBe(planPanelKey(['paddleocr']))
    expect(planPanelKey(['a', 'b'])).not.toBe(planPanelKey(['ab']))
    expect(planPanelKey(['a', 'b'])).toBe(planPanelKey(['a', 'b']))
  })
})
