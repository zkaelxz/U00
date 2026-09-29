import { expect, test, type Page, type Request } from '@playwright/test'

// Diagnostics admin sections and Settings > Browser extension (desktop).
// Every install, upgrade, reset and extension POST is mocked; a catch-all
// fails the test on any other non-GET /api call, so a real pip run or a
// real reset can never hit the shared seeded library.

const job = (o: Record<string, unknown> = {}) => ({
  job_id: 'translate_1', status: 'running', progress: 0.4, message: 'Batch 2 of 5', error: null,
  description: 'Translate Signal', gpu_touching: false, started_at: Date.now() / 1000 - 185, finished_at: null,
  updated_at: 0, ...o,
})

const overview = {
  dependencies: {
    jieba: { installed: true, powers: 'Chinese word segmentation', tier: 'feature' },
    pandas: { installed: true, powers: 'tables', tier: 'required' },
    'yt-dlp': { installed: false, powers: 'downloading video', tier: 'feature' },
    torch: { installed: false, powers: 'ML backends', tier: 'feature' },
  },
  file_completeness: { missing_top_level: [], missing_tabs: [], all_present: true },
  library_writable: true,
  gpu: { available: false, name: null, vram_used_gb: null, vram_total_gb: null, torch_cuda_version: null, message: 'No GPU.' },
  model_engine_versions: [],
  recent_log_lines: [],
}

const setup = (o: Record<string, unknown> = {}) => ({
  python: { version: '3.11.9', ok: true },
  ffmpeg: { found: true, version: '6.1' },
  js_runtime: { found: true, name: 'deno' },
  cuda: { torch_installed: false, cuda_available: null },
  files: { all_present: true, missing_top_level: [], missing_tabs: [] },
  library_writable: true,
  ...o,
})

// A held install is always answered by its mock, never left pending:
// Chromium lets a pending intercepted request through to the server when
// the page closes. afterEach releases it and waits for the answer to go out.
let releaseInstall: () => void = () => undefined
const pendingFulfils: Promise<void>[] = []
test.afterEach(async () => {
  releaseInstall()
  await Promise.allSettled(pendingFulfils.splice(0))
})

/** Catch-all first (Playwright tries the newest route first, so later mocks win). */
async function guard(page: Page): Promise<string[]> {
  const unmocked: string[] = []
  await page.route('**/api/**', (route) => {
    const r = route.request()
    if (r.method() === 'GET') return route.continue()
    unmocked.push(`${r.method()} ${r.url()}`)
    return route.abort()
  })
  return unmocked
}

async function mockPage(page: Page, o: { jobs?: unknown[]; setup?: unknown; stats?: unknown } = {}) {
  await page.route('**/api/diagnostics', (r) => r.fulfill({ json: overview }))
  await page.route('**/api/diagnostics/setup-checks', (r) => r.fulfill({ json: o.setup ?? setup() }))
  await page.route('**/api/jobs', (r) => r.fulfill({ json: { items: o.jobs ?? [], count: (o.jobs ?? []).length } }))
  if (o.stats) await page.route('**/api/library/stats', (r) => r.fulfill({ json: o.stats }))
}

const openSection = (page: Page, title: RegExp) => page.locator('summary', { hasText: title }).first().click()

test('keeps the testids, hides Jobs when empty, and opens Setup on a problem', async ({ page }) => {
  const unmocked = await guard(page)
  await mockPage(page, { setup: setup({ ffmpeg: { found: false, version: null } }) })
  await page.goto('/#/diagnostics')
  await expect(page.getByTestId('diagnostics-summary')).toHaveText('1 setup problem · 2 of 4 packages')
  await expect(page.getByTestId('system-summary')).toBeVisible() // core checks: always shown at the top
  await expect(page.getByTestId('system-summary')).toContainText('Problem: ffmpeg not found')
  await expect(page.getByTestId('dependency-panel')).toHaveCount(1)
  await expect(page.getByTestId('job-list')).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Jobs' })).toHaveCount(0)
  const header = await page.getByTestId('diagnostics-summary').boundingBox()
  expect(header!.height).toBeLessThan(30) // one line
  expect(unmocked).toEqual([])
})

test('install: two presses, PC-only header, every admin button waits, then the result', async ({ page }) => {
  const unmocked = await guard(page)
  await mockPage(page, { stats: { total_dramas: 3, total_lines: 10, by_status: {}, by_media_type: {}, translated_lines: 0, usage: {} } })
  const sent: Request[] = []
  // Held until release() (or afterEach); always fulfilled, never continued.
  const gate = new Promise<void>((res) => (releaseInstall = res))
  const release = () => releaseInstall()
  await page.route('**/api/diagnostics/dependencies/**', async (r) => {
    sent.push(r.request())
    const ok = r.request().url().includes('yt-dlp')
    const answer = gate.then(() => r.fulfill({
      json: { package: ok ? 'yt-dlp' : 'torch', ok, output_tail: ok ? ['Successfully installed'] : ['ERROR: no space'] },
    }))
    pendingFulfils.push(answer)
    await answer
  })
  await page.goto('/#/diagnostics')
  await openSection(page, /^Packages/)
  await openSection(page, /^Missing packages/)
  await openSection(page, /^Danger zone/)

  await page.getByRole('button', { name: 'Install yt-dlp' }).click()
  expect(sent).toHaveLength(0) // first press only arms
  await page.getByRole('button', { name: 'Confirm install yt-dlp' }).click()
  await expect(page.getByTestId('install-running')).toHaveText(
    'Installing yt-dlp… this can take several minutes. Keep this tab open.')
  expect(sent).toHaveLength(1)
  expect(sent[0].postDataJSON()).toEqual({ confirm: true })
  expect(sent[0].headers()['x-baihe-local']).toBe('1')
  await expect(page.getByTestId('diagnostics-summary')).toContainText('· Installing yt-dlp')
  // The running line is the reason (the "Wait…" line is hidden meanwhile) and describes the disabled buttons.
  await expect(page.getByTestId('dependency-panel')).not.toContainText('Wait for the install to finish.')
  const runningId = await page.getByTestId('install-running').getAttribute('id')
  await expect(page.getByRole('button', { name: 'Install torch' })).toHaveAttribute('aria-describedby', runningId!)
  await expect(page.getByRole('button', { name: 'Install torch' })).toBeDisabled()
  await expect(page.getByRole('button', { name: /Update jieba/ })).toHaveCount(0) // no Update before a check
  await page.getByLabel(/Type RESET to confirm/).fill('RESET')
  await expect(page.getByRole('button', { name: 'Reset library' })).toBeDisabled()
  await expect(page.locator('.danger-zone')).toContainText('Wait for the install to finish.')

  release()
  await expect(page.getByTestId('install-result')).toContainText('Installed yt-dlp.')
  await expect(page.getByText('Installed yt-dlp.')).toBeFocused()
  await expect(page.getByTestId('install-result').locator('details')).not.toHaveAttribute('open', '')
  await expect(page.getByTestId('install-running')).toHaveText('')

  // torch gets its size in the confirm step; a failed install opens Output.
  await page.getByRole('button', { name: 'Install torch' }).click()
  await page.getByRole('button', { name: 'Confirm install torch (about 2.5 GB)' }).click()
  await expect(page.getByTestId('install-result')).toContainText('Install failed for torch.')
  await expect(page.getByTestId('install-result').locator('details')).toHaveAttribute('open', '')
  await expect(page.getByTestId('install-result').locator('pre')).toHaveText('ERROR: no space')
  expect(unmocked).toEqual([])
})

test('install errors: 409 shows the server sentence, 404 the unknown-package line', async ({ page }) => {
  const unmocked = await guard(page)
  await mockPage(page)
  await page.route('**/api/diagnostics/dependencies/yt-dlp/install', (r) => r.fulfill({
    status: 409, json: { error: { code: 'conflict', message: 'A background job is running or queued; wait for it to finish.' } },
  }))
  await page.route('**/api/diagnostics/dependencies/torch/install', (r) => r.fulfill({
    status: 404, json: { error: { code: 'not_found', message: 'Unknown or non-installable package.' } },
  }))
  await page.goto('/#/diagnostics')
  await openSection(page, /^Packages/)
  await openSection(page, /^Missing packages/)
  await page.getByRole('button', { name: 'Install yt-dlp' }).click()
  await page.getByRole('button', { name: 'Confirm install yt-dlp' }).click()
  await expect(page.getByRole('alert')).toHaveText('A background job is running or queued; wait for it to finish.')
  await page.getByRole('button', { name: 'Install torch' }).click()
  await page.getByRole('button', { name: /Confirm install torch/ }).click()
  await expect(page.getByRole('alert')).toHaveText('Unknown or non-installable package.')
  expect(unmocked).toEqual([])
})

test('a running job blocks install and reset with a reason, and Jobs stays open', async ({ page }) => {
  const unmocked = await guard(page)
  await mockPage(page, {
    jobs: [job()],
    stats: { total_dramas: 3, total_lines: 10, by_status: {}, by_media_type: {}, translated_lines: 0, usage: {} },
  })
  await page.route('**/api/jobs/translate_1/cancel', (r) =>
    r.fulfill({ json: { job_id: 'translate_1', cancel_requested: true, status: 'running' } }))
  await page.goto('/#/diagnostics')
  await expect(page.getByTestId('diagnostics-summary')).toContainText('1 job running')
  await expect(page.getByTestId('job-list')).toBeVisible()
  await expect(page.getByTestId('job-list')).toContainText('Running 40%')
  await openSection(page, /^Packages/)
  await expect(page.getByTestId('dependency-panel')).toContainText('Wait for running jobs to finish before installing.')
  await openSection(page, /^Danger zone/)
  await expect(page.locator('.danger-zone')).toContainText('Stop running jobs first (see Jobs above).')
  await page.getByLabel(/Type RESET to confirm/).fill('RESET')
  await expect(page.getByRole('button', { name: 'Reset library' })).toBeDisabled()
  await page.getByRole('button', { name: 'Cancel' }).click()
  expect(unmocked).toEqual([])
})

test('reset: exact RESET, sends the confirm word, then says so with a link', async ({ page }) => {
  const unmocked = await guard(page)
  await mockPage(page, { stats: { total_dramas: 12, total_lines: 48210, by_status: {}, by_media_type: {}, translated_lines: 0, usage: {} } })
  const bodies: unknown[] = []
  await page.route('**/api/diagnostics/reset-library', (r) => {
    bodies.push(r.request().postDataJSON())
    return r.fulfill({ json: { ok: true, reset_at: 1 } })
  })
  await page.goto('/#/diagnostics')
  await openSection(page, /^Danger zone/)
  const zone = page.locator('.danger-zone')
  await expect(zone).toContainText('Currently 12 dramas, 48,210 lines.')
  const button = zone.getByRole('button', { name: 'Reset library' })
  await zone.getByLabel(/Type RESET to confirm/).fill('reset')
  await expect(button).toBeDisabled()
  await zone.getByLabel(/Type RESET to confirm/).fill('RESET')
  await expect(button).toBeEnabled()
  await button.click()
  await expect(page.getByTestId('reset-done')).toContainText('Library reset.')
  await expect(page.getByTestId('reset-done')).toBeFocused()
  await expect(page.getByRole('link', { name: 'Go to Library' })).toHaveAttribute('href', '#/library')
  expect(bodies).toEqual([{ confirm: true, confirm_text: 'RESET' }])
  expect(unmocked).toEqual([])
})

test('an empty library needs no reset', async ({ page }) => {
  const unmocked = await guard(page)
  await mockPage(page, { stats: { total_dramas: 0, total_lines: 0, by_status: {}, by_media_type: {}, translated_lines: 0, usage: {} } })
  await page.goto('/#/diagnostics')
  await openSection(page, /^Danger zone/)
  await expect(page.locator('.danger-zone')).toContainText('Library is already empty.')
  await expect(page.getByLabel(/Type RESET to confirm/)).toHaveCount(0)
  expect(unmocked).toEqual([])
})

test('away from the PC: no install, reset or extension controls and no extension calls', async ({ page }) => {
  const unmocked = await guard(page)
  await mockPage(page)
  const extensionCalls: string[] = []
  page.on('request', (r) => {
    if (r.url().includes('/api/extension')) extensionCalls.push(r.url())
  })
  await page.route('**/api/meta', (r) =>
    r.fulfill({ json: { app: 'Baihe Studio', api_version: '0.1', environment: 'production', local: false } }))
  await page.goto('/#/diagnostics')
  await openSection(page, /^Packages/)
  await openSection(page, /^Missing packages/)
  await expect(page.getByTestId('dependency-panel')).toContainText('Installing is PC only.')
  await expect(page.getByRole('button', { name: /^Install / })).toHaveCount(0)
  await openSection(page, /^Danger zone/)
  await expect(page.locator('.danger-zone')).toContainText('Run this on the main PC.')

  await page.goto('/#/settings')
  const ext = page.getByRole('region', { name: 'Browser extension' })
  await expect(ext.locator('.card-meta')).toHaveText('PC only')
  await expect(ext).toContainText('Run this on the main PC.')
  await page.waitForTimeout(300)
  expect(extensionCalls).toEqual([])
  expect(unmocked).toEqual([])
})

test('PC mode not yet known or unconfirmed: a muted line instead of install, reset and extension controls', async ({ page }) => {
  const unmocked = await guard(page)
  await mockPage(page)
  const extensionCalls: string[] = []
  page.on('request', (r) => {
    if (r.url().includes('/api/extension')) extensionCalls.push(r.url())
  })
  // /api/meta held (the mode stays 'unknown'), then failed. afterEach
  // releases it too, so it is always answered before the page closes.
  let answerMeta: () => void = () => undefined
  const held = new Promise<void>((go) => {
    answerMeta = go
    releaseInstall = go
  })
  let metaDone: () => void = () => undefined
  const metaAnswered = new Promise<void>((done) => {
    metaDone = done
  })
  pendingFulfils.push(metaAnswered)
  await page.route('**/api/meta', async (r) => {
    await held
    try {
      await r.fulfill({ status: 500, json: { error: { code: 'internal', message: 'down' } } })
    } finally {
      metaDone()
    }
  })
  await page.goto('/#/diagnostics')
  await openSection(page, /^Packages/)
  const panel = page.getByTestId('dependency-panel')
  await expect(panel).toContainText('Checking whether this is the main PC…')
  await openSection(page, /^Missing packages/)
  await expect(page.getByRole('button', { name: /^Install / })).toHaveCount(0)
  await openSection(page, /^Danger zone/)
  await expect(page.locator('.danger-zone')).toContainText('Checking whether this is the main PC…')
  await expect(page.locator('.danger-zone').getByRole('textbox')).toHaveCount(0)

  // /api/meta fails: say so, still no controls.
  answerMeta()
  await metaAnswered
  await expect(panel).toContainText("Couldn't confirm this is the main PC.")
  await expect(page.locator('.danger-zone')).toContainText("Couldn't confirm this is the main PC.")
  await expect(page.getByRole('button', { name: /^Install / })).toHaveCount(0)

  await page.goto('/#/settings')
  const ext = page.getByRole('region', { name: 'Browser extension' })
  await expect(ext).toContainText("Couldn't confirm this is the main PC.")
  await page.waitForTimeout(300)
  expect(extensionCalls).toEqual([])
  expect(unmocked).toEqual([])
})

test('log filter waits for typing to settle; an empty result says so', async ({ page }) => {
  const unmocked = await guard(page)
  await mockPage(page)
  const urls: string[] = []
  await page.route('**/api/diagnostics/log**', (r) => {
    urls.push(r.request().url())
    const kw = new URL(r.request().url()).searchParams.get('keyword')
    return r.fulfill({ json: { lines: kw ? [] : ['12:00 INFO started', '12:01 ERROR boom'] } })
  })
  await page.goto('/#/diagnostics')
  await openSection(page, /^Log/)
  await expect(page.getByLabel('Log lines')).toContainText('12:01 ERROR boom')
  await page.getByRole('searchbox', { name: 'Filter' }).pressSequentially('ERROR')
  await expect(page.getByTestId('log-empty')).toHaveText('No lines match.')
  const filtered = urls.filter((u) => u.includes('keyword'))
  expect(filtered).toHaveLength(1) // debounced: one request, not one per letter
  expect(filtered[0]).toMatch(/\/api\/diagnostics\/log\?n=50&keyword=ERROR$/)
  expect(unmocked).toEqual([])
})

test('check access asks online only when pressed and links to the terms', async ({ page }) => {
  const unmocked = await guard(page)
  await mockPage(page)
  const urls: string[] = []
  await page.route('**/api/diagnostics/pyannote**', (r) => {
    urls.push(r.request().url())
    const online = r.request().url().includes('check_access=true')
    return r.fulfill({ json: {
      pyannote_installed: true, hf_token_configured: true, ready: !online,
      models: online ? [{ model: 'pyannote/speaker-diarization-3.1', accessible: false }] : null,
    } })
  })
  await page.goto('/#/diagnostics')
  await expect(page.locator('summary', { hasText: 'Speaker detection' })).toContainText('Ready')
  await openSection(page, /^Speaker detection/)
  await page.getByRole('button', { name: 'Check access online' }).click()
  await expect(page.getByText('pyannote/speaker-diarization-3.1: terms not accepted')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Accept terms ↗' })).toHaveAttribute(
    'href', 'https://huggingface.co/pyannote/speaker-diarization-3.1')
  expect(urls.filter((u) => u.includes('check_access=true'))).toHaveLength(1)
  expect(unmocked).toEqual([])
})

test('check access says why when huggingface_hub is missing', async ({ page }) => {
  const unmocked = await guard(page)
  await mockPage(page)
  await page.route('**/api/diagnostics/pyannote**', (r) => r.fulfill({ json: {
    pyannote_installed: true, hf_token_configured: true, ready: true, models: null,
  } }))
  await page.goto('/#/diagnostics')
  await openSection(page, /^Speaker detection/)
  await expect(page.getByText("Can't check: huggingface_hub isn't installed.")).toHaveCount(0)
  await page.getByRole('button', { name: 'Check access online' }).click()
  await expect(page.getByText("Can't check: huggingface_hub isn't installed.")).toBeVisible()
  await expect(page.getByRole('list', { name: 'Gated models' })).toHaveCount(0)
  expect(unmocked).toEqual([])
})

test('extension: summary, two-step token reveal, never stored, Hide clears it', async ({ page }) => {
  const unmocked = await guard(page)
  await page.route('**/api/extension/status', (r) => r.fulfill({ json: { enabled: false, running: true } }))
  let tokenPosts = 0
  await page.route('**/api/extension/token', (r) => {
    tokenPosts += 1
    return r.fulfill({ json: { token: 'tok-e2e-123' }, headers: { 'Cache-Control': 'no-store' } })
  })
  const toggles: unknown[] = []
  await page.route('**/api/extension/enabled', (r) => {
    toggles.push(r.request().postDataJSON())
    return r.fulfill({ json: { enabled: true, running: true, restart_needed: false } })
  })
  await page.goto('/#/settings')
  const ext = page.getByRole('region', { name: 'Browser extension' })
  await expect(ext.locator('.card-meta')).toHaveText('Off · still running until Baihe restarts')
  // The status is the Card's meta line, next to the switch.
  await expect(ext.getByTestId('extension-note')).toHaveText('Off · still running until Baihe restarts')
  await expect(ext.getByTestId('extension-note')).toBeVisible()

  // Off: only the switch and the note; the engine picker and token appear once on.
  await expect(ext.getByRole('button', { name: 'Show extension token' })).toHaveCount(0)
  await ext.getByRole('switch', { name: 'Extension bridge' }).click()
  await expect(ext.getByRole('switch', { name: 'Extension bridge' })).toBeChecked()
  expect(toggles).toEqual([{ enabled: true }])

  await ext.getByRole('button', { name: 'Show extension token' }).click()
  expect(tokenPosts).toBe(0)
  await ext.getByRole('button', { name: 'Confirm show extension token' }).click()
  const input = ext.getByLabel('Extension token', { exact: true })
  await expect(input).toHaveValue('tok-e2e-123')
  await expect(input).toHaveAttribute('readonly', '')
  const stored = await page.evaluate(() =>
    JSON.stringify({ ...window.localStorage }) + JSON.stringify({ ...window.sessionStorage }))
  expect(stored).not.toContain('tok-e2e-123')

  await ext.getByRole('button', { name: 'Hide' }).click()
  await expect(ext.getByLabel('Extension token', { exact: true })).toHaveCount(0)
  await expect(ext.getByTestId('token-announce')).toHaveText('Token hidden.')
  await expect(page.locator('body')).not.toContainText('tok-e2e-123')
  expect(unmocked).toEqual([])
})

test('extension: pick the engine pages are translated with (key stays on the PC)', async ({ page }) => {
  const unmocked = await guard(page)
  await page.route('**/api/extension/status', (r) => r.fulfill({ json: { enabled: true, running: true } }))
  const engines = [
    { name: 'claude', label: 'Claude', free: false, models: ['claude-sonnet-5', 'claude-opus-4-8'], key_configured: false },
    { name: 'deepl', label: 'DeepL', free: false, models: null, key_configured: true },
  ]
  let current: Record<string, unknown> = { engine: null, model: null, ready: false, engines }
  const saves: unknown[] = []
  await page.route('**/api/extension/engine', (r) => {
    if (r.request().method() === 'POST') {
      const body = r.request().postDataJSON() as { engine: string | null; model: string | null }
      saves.push(body)
      current = { ...current, ...body, ready: body.engine === 'deepl' }
    }
    return r.fulfill({ json: current })
  })
  await page.goto('/#/settings')
  const ext = page.getByRole('region', { name: 'Browser extension' })
  const picker = ext.getByRole('combobox', { name: 'Translate pages with' })
  await expect(picker).toHaveValue('')
  await expect(ext.getByTestId('extension-engine-note')).toHaveText(
    'No engine: pages come back with their original text only.')
  await expect(picker.locator('option', { hasText: 'Claude (no key)' })).toHaveCount(1)

  await picker.selectOption('claude')
  await expect(ext.getByTestId('extension-engine-note')).toHaveText(
    'No Claude key is saved on this PC, so pages come back untranslated.')
  await ext.getByRole('combobox', { name: 'Model' }).selectOption('claude-opus-4-8')
  await expect(ext.getByRole('combobox', { name: 'Model' })).toHaveValue('claude-opus-4-8')

  await picker.selectOption('deepl')
  await expect(ext.getByTestId('extension-engine-note')).toHaveText(
    'Pages are translated with DeepL. The key stays on this PC.')
  await expect(ext.getByRole('combobox', { name: 'Model' })).toHaveCount(0)
  expect(saves).toEqual([
    { engine: 'claude', model: null },
    { engine: 'claude', model: 'claude-opus-4-8' },
    { engine: 'deepl', model: null },
  ])
  expect(unmocked).toEqual([])
})
