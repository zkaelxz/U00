import type { Page } from '@playwright/test'

// Mocks for the consolidated Diagnostics page: Setup folds and holds speaker
// detection and the one model list. Writes are never sent anywhere real: a
// catch-all aborts every non-GET /api call a test did not mock.

export const REV_WHISPER = 'a'.repeat(40)
export const REV_PYANNOTE = 'b'.repeat(40)
export const REV_OTHER = 'c'.repeat(40)

const setup = (ffmpegFound = true) => ({
  python: { version: '3.11.9', ok: true },
  ffmpeg: { found: ffmpegFound, version: '6.1', libass: true },
  js_runtime: { found: true, name: 'deno' },
  cuda: { torch_installed: false, cuda_available: null },
  files: { all_present: true, missing_top_level: [], missing_tabs: [] },
  library_writable: true,
})

const engine = (name: string, o: Record<string, unknown> = {}) =>
  ({ name, version: '1.0.0', url: null, installed: true, package: name.toLowerCase(), help: null, ...o })

const overview = {
  dependencies: {
    jieba: { installed: true, powers: 'Chinese word segmentation', tier: 'feature' },
    'yt-dlp': { installed: false, powers: 'downloading video', tier: 'feature' },
    paddleocr: { installed: false, powers: 'OCR (PaddleOCR backend)', tier: 'feature' },
    'moss-transcribe-diarize': { installed: false, powers: 'experimental one-pass transcription', tier: 'experimental' },
  },
  file_completeness: { missing_top_level: [], missing_tabs: [], all_present: true },
  library_writable: true,
  gpu: { available: false, name: null, vram_used_gb: null, vram_total_gb: null, torch_cuda_version: null, message: 'No GPU.' },
  model_engine_versions: [
    engine('Whisper (faster-whisper)', { version: '1.1.0' }),
    engine('Qwen3-ASR', { version: 'not installed', installed: false }),
    engine('SenseVoice (FunASR)', { version: '1.2.0' }),
    engine('pyannote diarization model', { package: null, version: 'pyannote/speaker-diarization-3.1' }),
  ],
  recent_log_lines: [],
}

const pkg = (name: string, o: Record<string, unknown> = {}) => ({
  name, dist: name, installed: false, installable: true, powers: '', approx_mb: 10, pulls_torch: false,
  source_url: null, not_offered_reason: null, warning: null, ...o,
})

const presets = {
  tasks: [
    {
      id: 'hardsub_ocr', group: 'Video', label: 'Read burned-in captions (OCR)', help: 'Hard subtitles.',
      packages: ['jieba', 'paddleocr'], installed_count: 1, roles: { jieba: 'required', paddleocr: 'optional' },
      required_missing: [], optional_missing: ['paddleocr'], to_install: [], approx_mb: 0,
    },
    {
      id: 'url_import', group: 'Video', label: 'Import from a URL', help: 'Download video.',
      packages: ['yt-dlp'], installed_count: 0, to_install: ['yt-dlp'], approx_mb: 10,
    },
  ],
  packages: {
    jieba: pkg('jieba', { installed: true }),
    paddleocr: pkg('paddleocr', { approx_mb: 600, source_url: 'https://pypi.org/project/paddleocr/' }),
    'yt-dlp': pkg('yt-dlp'),
    'moss-transcribe-diarize': pkg('moss-transcribe-diarize', {
      installable: false, not_offered_reason: "not offered: it isn't on PyPI. It installs from its GitHub repository.",
    }),
  },
}

export function cacheMock() {
  return {
    hf_cache: [
      { repo_id: 'Systran/faster-whisper-large-v3', repo_type: 'model', revision: REV_WHISPER, size_bytes: 3_100_000_000 },
      { repo_id: 'pyannote/speaker-diarization-3.1', repo_type: 'model', revision: REV_PYANNOTE, size_bytes: 20_000_000 },
      { repo_id: 'someone/unknown-model', repo_type: 'model', revision: REV_OTHER, size_bytes: 1_000_000 },
    ],
    hf_total_bytes: 3_121_000_000,
    model_files: [{ folder: 'torch', name: 'model.pt', size_bytes: 84_000_000 }],
    model_files_total_bytes: 84_000_000,
  }
}

export async function guardWrites(page: Page): Promise<string[]> {
  const unmocked: string[] = []
  await page.route('**/api/**', (route) => {
    const r = route.request()
    if (r.method() === 'GET') return route.continue()
    unmocked.push(`${r.method()} ${r.url()}`)
    return route.abort()
  })
  return unmocked
}

export async function mockDiagnostics(page: Page, o: { ffmpegFound?: boolean; cache?: unknown; gpuState?: 'recommended' | 'cpu_on_gpu' } = {}) {
  await page.route('**/api/diagnostics', (r) => r.fulfill({ json: overview }))
  await page.route('**/api/diagnostics/setup-checks', (r) => r.fulfill({ json: setup(o.ffmpegFound ?? true) }))
  await page.route('**/api/jobs', (r) => r.fulfill({ json: { items: [], count: 0 } }))
  await page.route('**/api/diagnostics/install-presets', (r) => r.fulfill({ json: presets }))
  await page.route('**/api/diagnostics/model-cache', (r) => r.fulfill({ json: o.cache ?? cacheMock() }))
  await page.route('**/api/diagnostics/pyannote**', (r) => r.fulfill({ json: {
    pyannote_installed: true, hf_token_configured: true, ready: true, models: null,
  } }))
  const state = o.gpuState ?? 'recommended'
  const rec = {
    variant: 'cu128', label: 'NVIDIA GPU (CUDA 12.8)', index_url: 'https://download.pytorch.org/whl/cu128', needs_nvidia: true,
    versions: { torch: '2.11.0+cu128', torchvision: '0.26.0+cu128', torchaudio: '2.11.0+cu128' },
  }
  await page.route((u) => u.pathname === '/api/diagnostics/gpu-torch', (r) => r.fulfill({ json: {
    nvidia: { found: true, gpu_name: 'RTX 3080 Ti', driver_version: '581.42', status: 'ok', recommended: '570.65', minimum: '528.33' },
    installed: [{ name: 'torch', version: '2.11.0+cu128', build: state === 'recommended' ? 'cuda' : 'cpu' }],
    problems: [], state, python_supported: true, recommended: rec, variants: [rec], probe: null,
  } }))
}
