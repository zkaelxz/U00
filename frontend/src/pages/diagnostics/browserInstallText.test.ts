import { describe, expect, it } from 'vitest'

import type { BrowserInstallStatus } from '../../types/browserInstall'
import { browserResultLine, browserStatusLine, canOfferBrowser, showBrowser } from './browserInstallText'

const status = (o: Partial<BrowserInstallStatus> = {}): BrowserInstallStatus => ({
  playwright_installed: true, app_browser_installed: false, system_browser_found: false, free_mb: 5000,
  required_mb: 300, refusal: null, job_id: 'browser_install', job: null, last_result: null, ...o,
})
const running = { status: 'running', progress: 0.4, message: 'Downloading the browser…', error: null }

describe('Install browser support row', () => {
  it('shows only when Chrome or Edge is missing, or there is a job or result', () => {
    expect(showBrowser(null)).toBe(false)
    expect(showBrowser(status())).toBe(true)
    expect(showBrowser(status({ system_browser_found: true }))).toBe(false)
    expect(showBrowser(status({ system_browser_found: true, job: running }))).toBe(true)
    expect(showBrowser(status({ system_browser_found: true, last_result: { ok: true, message: 'x', output_tail: [] } }))).toBe(true)
  })

  it('offers the install only when the server has no refusal and nothing runs', () => {
    expect(canOfferBrowser(status())).toBe(true)
    expect(canOfferBrowser(status({ refusal: 'Install the playwright package first (Diagnostics > Packages).' }))).toBe(false)
    expect(canOfferBrowser(status({ job: running }))).toBe(false)
  })

  it('says what is installed or why not, and nothing while a job runs', () => {
    expect(browserStatusLine(status())).toBeNull()
    expect(browserStatusLine(status({ app_browser_installed: true, refusal: 'Browser support is already installed.' })))
      .toBe('Browser support is installed.')
    expect(browserStatusLine(status({ refusal: 'Not enough free disk space (about 300 MB needed).' })))
      .toBe('Not enough free disk space (about 300 MB needed).')
    expect(browserStatusLine(status({ job: running }))).toBeNull()
  })

  it('reports the last result, an error with its message', () => {
    expect(browserResultLine(status())).toBeNull()
    expect(browserResultLine(status({ last_result: { ok: true, message: 'Browser support installed.', output_tail: [] } })))
      .toEqual({ text: 'Browser support installed.', tone: 'ok' })
    expect(browserResultLine(status({ last_result: { ok: false, message: 'The browser download failed; see the output.', output_tail: [] } })))
      .toEqual({ text: 'The browser download failed; see the output.', tone: 'error' })
    expect(browserResultLine(status({ job: running, last_result: { ok: false, message: 'x', output_tail: [] } }))).toBeNull()
  })
})
