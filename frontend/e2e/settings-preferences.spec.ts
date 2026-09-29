import { expect, test, type Page } from '@playwright/test'

// Settings > the persisted preferences (defaults for new dramas, spending,
// OCR, offline, downloads, server addresses) and Appearance. GET/POST
// /api/settings and the endpoint routes are mocked with a small stateful
// fixture; a catch-all aborts (and records) any other non-GET /api call,
// so nothing is written to the seeded library or its .env.

const PREFS = {
  default_engine: 'claude',
  default_locale: 'en-US',
  default_style_note: '',
  episode_summary_engine: 'ollama',
  monthly_cap_usd: null as number | null,
  ollama_num_ctx_override: 0,
  whisper_model_path: '',
  ocr_backend: 'auto',
  ocr_prefer_paddle_vl_manga: false,
  tesseract_cmd: '',
  cookies_browser: null as string | null,
  cookies_file: '',
  whisper_model_path_configured: false,
  tesseract_cmd_configured: false,
  cookies_file_configured: false,
}

function overview() {
  return {
    engine_keys: { claude: false, ollama_url: false, libretranslate_url: false, gpt_sovits_url: false },
    gpu_limit_enabled: true,
    notify_on_completion: false,
    use_gpu: false,
    gemini_free_tier: false,
    preferences: { ...PREFS },
    endpoints: { ollama_url: null as string | null, libretranslate_url: null, gpt_sovits_url: null },
    monthly_cap_env_usd: 0,
    effective_monthly_cap_usd: 0,
    choices: {
      engines: ['claude', 'deepseek', 'gemini', 'ollama'],
      locales: ['en-US', 'en-GB', 'en-AU'],
      summary_engines: ['ollama', 'claude', 'deepseek', 'gemini'],
      ocr_backends: ['auto', 'manga_ocr', 'paddle', 'paddle_vl_manga', 'tesseract'],
      cookie_browsers: ['chrome', 'firefox', 'edge'],
    },
  }
}

async function mockSettings(page: Page) {
  const state = overview()
  const posts: { path: string; body: Record<string, unknown> }[] = []
  const unmocked: string[] = []
  await page.route('**/api/**', (route) => {
    const r = route.request()
    if (r.method() === 'GET') return route.fallback()
    unmocked.push(`${r.method()} ${r.url()}`)
    return route.abort()
  })
  await page.route('**/api/settings', (route) => {
    const r = route.request()
    if (r.method() === 'GET') return route.fulfill({ json: state })
    const body = r.postDataJSON() as Record<string, unknown>
    posts.push({ path: '/api/settings', body })
    Object.assign(state.preferences, body)
    if ('monthly_cap_usd' in body) state.effective_monthly_cap_usd = Number(body.monthly_cap_usd ?? 0)
    return route.fulfill({ json: state })
  })
  await page.route('**/api/settings/endpoints/**', (route) => {
    const path = new URL(route.request().url()).pathname
    const body = route.request().postDataJSON() as Record<string, unknown>
    posts.push({ path, body })
    const name = path.split('/')[4] as 'ollama_url'
    const url = path.endsWith('/clear') ? null : String(body.url)
    state.endpoints[name] = url
    state.engine_keys[name] = url !== null
    return route.fulfill({ json: { name, url, configured: url !== null } })
  })
  return { state, posts, unmocked }
}

async function open(page: Page, title: string) {
  const section = page.locator('details.section', { has: page.locator('.section-title', { hasText: new RegExp(`^${title}$`) }) })
  await section.locator('summary').click()
  await expect(section).toHaveAttribute('open', '')
  return section
}

test.afterEach(async ({ page }) => {
  await page.evaluate(() => {
    for (const k of Object.keys(localStorage)) if (k.startsWith('baihe.section.settings.') || k === 'baihe.theme') localStorage.removeItem(k)
  })
})

test('defaults for new dramas save only what changed', async ({ page }) => {
  const { posts, unmocked } = await mockSettings(page)
  await page.goto('/#/settings')
  const s = await open(page, 'Defaults for new dramas')
  const save = s.getByRole('button', { name: 'Save' })
  await expect(save).toBeDisabled()
  await s.getByLabel('Translation engine', { exact: true }).selectOption('deepseek')
  await s.getByLabel('English variant', { exact: true }).selectOption('en-GB')
  await s.getByLabel('Style note', { exact: true }).fill('Keep it short.')
  await save.click()
  await expect(s.getByRole('status')).toHaveText('Saved.')
  expect(posts).toEqual([
    { path: '/api/settings', body: { default_engine: 'deepseek', default_locale: 'en-GB', default_style_note: 'Keep it short.' } },
  ])
  await expect(save).toBeDisabled()
  await s.locator('summary').click()
  await expect(s.locator('.section-summary')).toHaveText('deepseek · en-GB · style note')
  expect(unmocked).toEqual([])
})

test('spending, offline and OCR fields check input before saving', async ({ page }) => {
  const { posts, unmocked } = await mockSettings(page)
  await page.goto('/#/settings')

  const spend = await open(page, 'Spending')
  await spend.getByLabel('Monthly cap', { exact: true }).fill('lots')
  await spend.getByRole('button', { name: 'Save' }).click()
  await expect(spend.getByRole('alert')).toContainText('Enter an amount')
  expect(posts).toEqual([])
  await spend.getByLabel('Monthly cap', { exact: true }).fill('12.50')
  await spend.getByRole('button', { name: 'Save' }).click()
  await expect(spend.getByTestId('cap-effective')).toHaveText('Cap in effect: $12.50 a month.')

  const offline = await open(page, 'Offline and performance')
  await offline.getByLabel('Ollama context window', { exact: true }).fill('1.5')
  await offline.getByRole('button', { name: 'Save' }).click()
  await expect(offline.getByRole('alert')).toContainText('whole number')
  await offline.getByLabel('Ollama context window', { exact: true }).fill('16384')
  await offline.getByLabel('Offline Whisper model folder', { exact: true }).fill('D:\\models\\whisper-small')
  await offline.getByRole('button', { name: 'Save' }).click()
  await expect(offline.getByRole('status')).toHaveText('Saved.')

  const ocr = await open(page, 'OCR')
  await ocr.getByLabel('Default backend', { exact: true }).selectOption('manga_ocr')
  await ocr.getByRole('switch', { name: 'Japanese: prefer PaddleOCR-VL' }).click()
  await ocr.getByLabel('Tesseract program', { exact: true }).fill('  C:\\Tess\\tesseract.exe ')
  await ocr.getByRole('button', { name: 'Save' }).click()
  await expect(ocr.getByRole('status')).toHaveText('Saved.')

  const dl = await open(page, 'Downloads')
  await dl.getByLabel('Cookies from browser', { exact: true }).selectOption('firefox')
  await dl.getByRole('button', { name: 'Save' }).click()
  await expect(dl.getByRole('status')).toHaveText('Saved.')

  expect(posts.map((p) => p.body)).toEqual([
    { monthly_cap_usd: 12.5 },
    { whisper_model_path: 'D:\\models\\whisper-small', ollama_num_ctx_override: 16384 },
    { ocr_backend: 'manga_ocr', ocr_prefer_paddle_vl_manga: true, tesseract_cmd: 'C:\\Tess\\tesseract.exe' },
    { cookies_browser: 'firefox' },
  ])
  expect(unmocked).toEqual([])
})

test('server addresses: a URL with a password is refused client-side; save and clear', async ({ page }) => {
  const { posts, unmocked } = await mockSettings(page)
  await page.goto('/#/settings')
  const s = await open(page, 'Server addresses')
  const ollama = s.getByTestId('endpoint-ollama_url')
  await ollama.getByLabel('Ollama URL', { exact: true }).fill('http://me:hunter2@192.168.1.5:11434')
  await ollama.getByRole('button', { name: 'Save' }).click()
  await expect(ollama.getByRole('alert')).toContainText('user name or password')
  expect(posts).toEqual([])
  await ollama.getByLabel('Ollama URL', { exact: true }).fill('http://192.168.1.5:11434')
  await ollama.getByRole('button', { name: 'Save' }).click()
  await expect(ollama.getByRole('status')).toHaveText('Saved.')
  await ollama.getByRole('button', { name: 'Clear' }).click()
  await expect(ollama.getByRole('status')).toHaveText('Cleared.')
  await expect(ollama.getByLabel('Ollama URL', { exact: true })).toHaveValue('')
  expect(posts).toEqual([
    { path: '/api/settings/endpoints/ollama_url', body: { url: 'http://192.168.1.5:11434', confirm: true } },
    { path: '/api/settings/endpoints/ollama_url/clear', body: { confirm: true } },
  ])
  expect(unmocked).toEqual([])
})

test('appearance: dark and light apply at once and survive a reload', async ({ page }) => {
  await mockSettings(page)
  await page.emulateMedia({ colorScheme: 'light' })
  await page.goto('/#/settings')
  const bg = () => page.evaluate(() => getComputedStyle(document.body).backgroundColor)
  const light = await bg()
  const s = await open(page, 'Appearance')
  await s.getByLabel('Theme', { exact: true }).selectOption('dark')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  expect(await bg()).not.toBe(light)
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')

  // Light wins over a dark system setting.
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.locator('details.section', { hasText: 'Appearance' }).getByLabel('Theme', { exact: true }).selectOption('light')
  expect(await bg()).toBe(light)
  await page.locator('details.section', { hasText: 'Appearance' }).getByLabel('Theme', { exact: true }).selectOption('system')
  await expect(page.locator('html')).not.toHaveAttribute('data-theme', /.*/)
  expect(await bg()).not.toBe(light)
})

test('away from the PC the preference blocks say PC only; Appearance still works', async ({ page }) => {
  const { unmocked } = await mockSettings(page)
  await page.route('**/api/meta', (route) =>
    route.fulfill({ json: { app: 'baihe', api_version: '1', environment: 'development', local: false } }),
  )
  await page.goto('/#/settings')
  for (const title of ['Defaults for new dramas', 'Spending', 'OCR', 'Offline and performance', 'Downloads', 'Server addresses']) {
    const s = page.locator('details.section', { has: page.locator('.section-title', { hasText: new RegExp(`^${title}$`) }) })
    await expect(s.locator('.section-summary')).toHaveText('PC only')
  }
  const a = await open(page, 'Appearance')
  await a.getByLabel('Theme', { exact: true }).selectOption('dark')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  expect(unmocked).toEqual([])
})
