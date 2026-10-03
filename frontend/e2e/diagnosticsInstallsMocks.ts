import type { Page, Request } from '@playwright/test'

import { ME } from './authMocks'

// Mocks for Diagnostics > Setup "Install Deno" and Packages "Test first"
// (both server jobs, polled) plus the voice-bank Play. Every POST is
// mocked; guard() records and aborts any other non-GET /api call.

const overview = {
  dependencies: {
    pypinyin: { installed: true, powers: 'Chinese pinyin', tier: 'feature' },
    pandas: { installed: true, powers: 'tables', tier: 'required' },
  },
  file_completeness: { missing_top_level: [], missing_tabs: [], all_present: true },
  library_writable: true,
  gpu: { available: false, name: null, vram_used_gb: null, vram_total_gb: null, torch_cuda_version: null, message: 'No GPU.' },
  model_engine_versions: [],
  recent_log_lines: [],
}

const setup = (jsFound: boolean) => ({
  python: { version: '3.12.4', ok: true },
  ffmpeg: { found: true, version: '6.1' },
  js_runtime: jsFound ? { found: true, name: 'deno' } : { found: false, name: null },
  cuda: { torch_installed: false, cuda_available: null },
  files: { all_present: true, missing_top_level: [], missing_tabs: [] },
  library_writable: true,
})

const UPDATES = {
  checked_at: 1_790_000_000,
  packages: {
    pypinyin: {
      name: 'pypinyin', dist: 'pypinyin', installed_version: '0.53.0', status: 'update', latest: '0.55.0',
      target: '0.55.0', reason: null,
    },
  },
}

const denoStatus = (o: Record<string, unknown> = {}) => ({
  runtime_found: false, runtime_name: null, deno_on_path: false, deno_installed: false, can_install: true,
  install_method: 'download', job_id: 'deno_install', job: null, last_result: null, ...o,
})

const running = (progress: number, message: string) => ({ status: 'running', progress, message, error: null })
const doneJob = { status: 'done', progress: 1, message: 'Deno v2.9.7 installed.', error: null }

type Mocks = { sent: Request[]; unmocked: string[] }

export async function guard(page: Page): Promise<Mocks> {
  const m: Mocks = { sent: [], unmocked: [] }
  await page.route('**/api/**', (route) => {
    const r = route.request()
    if (r.method() === 'GET') return route.continue()
    m.unmocked.push(`${r.method()} ${r.url()}`)
    return route.abort()
  })
  await page.route((u) => u.pathname === '/api/auth/me', (r) => r.fulfill({ json: ME.authOff }))
  return m
}

/**
 * Diagnostics with no JS runtime. The Deno install and the Test first run
 * each go: idle -> (POST) running 40% -> running 80% -> done.
 */
export async function mockDiagnostics(page: Page, m: Mocks, o: { jsFound?: boolean } = {}) {
  let denoStep = -1
  let testStep = -1
  await page.route('**/api/diagnostics', (r) => r.fulfill({ json: overview }))
  await page.route('**/api/diagnostics/setup-checks', (r) =>
    r.fulfill({ json: setup(o.jsFound ?? false) }))
  await page.route('**/api/diagnostics/install-presets', (r) => r.fulfill({ json: { tasks: [], packages: {} } }))
  await page.route('**/api/jobs', (r) => r.fulfill({ json: { items: [], count: 0 } }))
  await page.route((u) => u.pathname === '/api/diagnostics/gpu-torch', (r) => r.fulfill({ status: 404, json: {} }))
  await page.route((u) => u.pathname === '/api/diagnostics/package-updates/check', (r) => {
    m.sent.push(r.request())
    return r.fulfill({ json: UPDATES })
  })
  await page.route((u) => u.pathname === '/api/diagnostics/deno', (r) => {
    if (denoStep >= 0) denoStep += 1
    const json = denoStep < 0 ? denoStatus()
      : denoStep <= 1 ? denoStatus({ job: running(0.4, 'Downloading Deno v2.9.7... 40%') })
        : denoStep === 2 ? denoStatus({ job: running(0.8, 'Downloading Deno v2.9.7... 80%') })
          : denoStatus({
            deno_installed: true, job: doneJob,
            last_result: {
              ok: true, on_path: false, needs_restart: true, output_tail: ['Checksum OK. Unpacking...'],
              message: 'Deno was installed. Restart Baihe (and its terminal) so it is found on PATH.',
            },
          })
    return r.fulfill({ json })
  })
  await page.route((u) => u.pathname === '/api/diagnostics/deno/install', (r) => {
    m.sent.push(r.request())
    denoStep = 0
    return r.fulfill({ json: { job_id: 'deno_install', started: true } })
  })
  await page.route((u) => u.pathname === '/api/diagnostics/upgrade-check', (r) => {
    if (testStep >= 0) testStep += 1
    const base = { package: 'pypinyin', target: '0.55.0', job_id: 'upgrade_check' }
    const json = testStep < 0
      ? { package: null, target: null, output_tail: [], result: null, job_id: 'upgrade_check', job: null }
      : testStep <= 2
        ? { ...base, output_tail: ['Installing pypinyin==0.55.0 into it...'], result: null,
          job: running(0.35, "Running this app's test suite against pypinyin 0.55.0...") }
        : {
          ...base, output_tail: ['1 failed, 400 passed'], job: { ...doneJob, message: '' },
          result: {
            ok: false, verdict: 'broken', reason: '1 test fails with pypinyin 0.55.0 and passes today',
            version: '0.55.0', new_failures: ['tests/test_pinyin.py::test_tone'], preexisting_failures: [], conflicts: [],
          },
        }
    return r.fulfill({ json })
  })
  await page.route((u) => u.pathname === '/api/diagnostics/dependencies/pypinyin/test-upgrade', (r) => {
    m.sent.push(r.request())
    testStep = 0
    return r.fulfill({ json: { job_id: 'upgrade_check', started: true } })
  })
}

export const openSection = async (page: Page, title: RegExp) => {
  // Some sections now start open; click only a closed one, as a user would.
  const summary = page.locator('summary', { hasText: title }).first()
  if ((await summary.locator('xpath=..').getAttribute('open')) === null) await summary.click()
}
