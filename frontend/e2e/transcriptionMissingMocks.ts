import type { Page } from '@playwright/test'

import { ME } from './authMocks'

// Fresh install without faster-whisper vs. with it: the Transcribe card's
// config and the Diagnostics Packages data. Non-GET /api calls are refused.

const pkg = (name: string, installed: boolean) => ({
  name, dist: name, installed, installable: !installed, powers: '', approx_mb: 80, pulls_torch: false,
  source_url: `https://pypi.org/project/${name}/`, not_offered_reason: null, warning: null,
})

const CPU = {
  variant: 'cpu', label: 'CPU only', index_url: 'https://download.pytorch.org/whl/cpu',
  versions: { torch: '2.11.0', torchvision: '0.26.0', torchaudio: '2.11.0' }, needs_nvidia: false,
}

// `installedNow` may be a function so a spec can flip the answer once its fake install has run.
export async function mockTranscription(page: Page, installedNow: boolean | (() => boolean)): Promise<{ sent: string[] }> {
  const state = () => (typeof installedNow === 'function' ? installedNow() : installedNow)
  const sent: string[] = []
  await page.route('**/api/**', (route) => {
    const r = route.request()
    if (r.method() === 'GET') return route.continue()
    sent.push(`${r.method()} ${new URL(r.url()).pathname}`)
    return route.abort()
  })
  await page.route((u) => u.pathname === '/api/auth/me', (r) => r.fulfill({ json: ME.authOff }))
  await page.route('**/api/transcribe/dramas/1/config', async (route) => {
    const resp = await route.fetch()
    await route.fulfill({
      response: resp,
      json: { ...(await resp.json()), transcript_mode: 'whisper', asr_backend_choice: 'whisper', whisper_installed: state() },
    })
  })
  await page.route('**/api/diagnostics', (r) => { const installed = state(); return r.fulfill({
    json: {
      dependencies: {
        faster_whisper: { installed, powers: 'speech to text', tier: 'feature' },
        pandas: { installed: true, powers: 'tables', tier: 'required' },
      },
      file_completeness: { missing_top_level: [], all_present: true },
      library_writable: true,
      gpu: { available: false, name: null, vram_used_gb: null, vram_total_gb: null, torch_cuda_version: null, message: 'No GPU.' },
      model_engine_versions: [],
      recent_log_lines: [],
    },
  }) })
  await page.route('**/api/diagnostics/setup-checks', (r) => r.fulfill({
    json: {
      python: { version: '3.12.4', ok: true }, ffmpeg: { found: true, version: '6.1' },
      js_runtime: { found: true, name: 'deno' }, cuda: { torch_installed: false, cuda_available: null },
      files: { all_present: true, missing_top_level: [] }, library_writable: true,
    },
  }))
  await page.route('**/api/diagnostics/install-presets', (r) => { const installed = state(); return r.fulfill({
    json: {
      tasks: [{
        id: 'transcribe', group: 'Audio', label: 'Transcribe speech (Whisper)', help: 'Turn a title\'s audio into timed lines.',
        packages: ['faster_whisper'], installed_count: installed ? 1 : 0, to_install: installed ? [] : ['faster_whisper'],
        approx_mb: 80,
      }],
      packages: { faster_whisper: pkg('faster_whisper', installed) },
    },
  }) })
  await page.route('**/api/jobs', (r) => r.fulfill({ json: { items: [], count: 0 } }))
  await page.route((u) => u.pathname === '/api/diagnostics/gpu-torch', (r) => r.fulfill({
    json: {
      nvidia: { found: false, gpu_name: null, driver_version: null, status: 'none', recommended: '570.65', minimum: '528.33' },
      installed: [], problems: [], state: 'not_installed', python_supported: true, recommended: CPU, variants: [CPU], probe: null,
    },
  }))
  return { sent }
}
