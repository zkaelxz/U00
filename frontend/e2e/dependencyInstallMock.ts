import type { Page, Request } from '@playwright/test'

// The package install and GPU PyTorch setup are a server job: the page POSTs
// to start it, then polls GET /api/diagnostics/dependency-install. This mocks
// all three routes (plus Cancel), so a spec says only what pip "returns".
// With `hold`, the job stays running until that promise resolves.

export type FakeInstallResult = {
  ok: boolean
  output_tail: string[]
  hint?: string | null
  variant?: string | null
  verify?: unknown
}

export type InstallMock = {
  /** The POSTs that started a job (install and GPU setup), in order. */
  started: Request[]
  /** Cancel requests for the install job. */
  cancelled: Request[]
}

export async function mockDependencyInstall(
  page: Page,
  resultFor: (pkg: string) => FakeInstallResult,
  opts: { hold?: Promise<void>; onStart?: (r: Request) => void } = {},
): Promise<InstallMock> {
  const mock: InstallMock = { started: [], cancelled: [] }
  let current: { kind: 'package' | 'gpu_torch'; pkg: string } | null = null
  let held = !!opts.hold
  let cancelRequested = false
  void opts.hold?.then(() => { held = false })

  const start = (kind: 'package' | 'gpu_torch', pkg: string) => (route: { request(): Request; fulfill(o: object): Promise<void> }) => {
    mock.started.push(route.request())
    opts.onStart?.(route.request())
    current = { kind, pkg }
    cancelRequested = false
    return route.fulfill({ json: { job_id: 'dependency_install', started: true } })
  }
  await page.route('**/api/diagnostics/dependencies/*/install', (route) => {
    const pkg = decodeURIComponent(route.request().url().split('/dependencies/')[1].split('/')[0])
    return start('package', pkg)(route)
  })
  await page.route((u) => u.pathname === '/api/diagnostics/gpu-torch/setup', (route) => start('gpu_torch', 'torch')(route))
  await page.route((u) => u.pathname === '/api/jobs/dependency_install/cancel', (route) => {
    mock.cancelled.push(route.request())
    cancelRequested = true
    return route.fulfill({ json: { job_id: 'dependency_install', cancel_requested: true, status: 'running' } })
  })
  await page.route((u) => u.pathname === '/api/diagnostics/dependency-install', (route) => {
    if (!current) {
      return route.fulfill({ json: { kind: null, package: null, result: null, job_id: 'dependency_install', job: null } })
    }
    const base = { kind: current.kind, package: current.pkg, job_id: 'dependency_install' }
    if (cancelRequested) {
      return route.fulfill({
        json: {
          ...base, job: { status: 'cancelled', progress: 0.3, message: '', error: null },
          result: {
            package: current.pkg, ok: false, output_tail: ['Downloading wheel'], cancelled: true,
            hint: 'Cancelled. If pip was part-way through replacing files, run the install again to finish it.',
            variant: null, verify: null,
          },
        },
      })
    }
    if (held) {
      return route.fulfill({
        json: { ...base, result: null, job: { status: 'running', progress: 0.3, message: 'Downloading wheel', error: null } },
      })
    }
    const r = resultFor(current.pkg)
    return route.fulfill({
      json: {
        ...base,
        job: { status: r.ok ? 'done' : 'error', progress: 1, message: '', error: r.ok ? null : 'The install did not finish; see its output.' },
        result: { package: current.pkg, hint: null, variant: null, verify: null, ...r, cancelled: false },
      },
    })
  })
  return mock
}
