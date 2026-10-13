import { renderToStaticMarkup } from 'react-dom/server'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Need } from './needs'

const state = { admin: true, pc: 'local' as 'local' | 'remote' | 'unknown' }
vi.mock('../../hooks/useHolds', () => ({ useHolds: () => state.admin }))
vi.mock('../../hooks/usePcOnly', () => ({ usePcOnly: () => state.pc }))

import { ASK_OWNER, NeedsInstall, packagesHref } from './NeedsInstall'

const need = (over: Partial<Need>): Need => ({ id: 'x', label: 'X', reason: 'X is missing.', action: 'diagnostics', packages: [], ...over })
const html = (n: Need) => renderToStaticMarkup(<NeedsInstall need={n} />)

describe('NeedsInstall', () => {
  beforeEach(() => {
    state.admin = true
    state.pc = 'local'
  })

  it('says why, and opens Diagnostics > Packages with the package picked', () => {
    const out = html(need({ id: 'separation', reason: "Separating vocals first needs audio-separator, which isn't installed.", packages: ['audio-separator'] }))
    expect(out).toContain("Separating vocals first needs audio-separator, which isn&#x27;t installed.")
    expect(out).toContain('href="#/diagnostics?card=packages&amp;package=audio-separator"')
    expect(out).toContain('Open Diagnostics')
  })

  it('opens the Packages card without a pick when nothing is named', () => {
    expect(packagesHref({ packages: [] })).toBe('#/diagnostics?card=packages')
  })

  it('installs Whisper in place, with the two-step confirmation', () => {
    const out = html(need({ id: 'whisper', action: 'install_whisper', packages: ['faster_whisper'] }))
    expect(out).toContain('Install…')
    expect(out).not.toContain('Open Diagnostics')
  })

  it('offers GPU PyTorch as a calm suggestion with its size', () => {
    const out = html(need({ id: 'gpu_torch', action: 'install_gpu_torch', recommended: true, size: 'about 2.5 GB', reason: 'An NVIDIA GPU was found.' }))
    expect(out).toContain('Set up GPU…')
    expect(out).toContain('(about 2.5 GB)')
    expect(out).toContain('class="muted"')
    expect(out).not.toContain('class="warn"')
  })

  it('sends a missing key to Settings', () => {
    const out = html(need({ id: 'groq_key', action: 'settings', reason: 'Use Groq needs a Groq API key. Add it in Settings.' }))
    expect(out).toContain('href="#/settings"')
    expect(out).toContain('Add in Settings')
  })

  it('links the model terms beside a token fix', () => {
    const out = html(need({ id: 't', action: 'settings', link: { href: 'https://huggingface.co/pyannote/x', label: 'Accept the model terms' } }))
    expect(out).toContain('href="https://huggingface.co/pyannote/x"')
    expect(out).toContain('rel="noopener noreferrer"')
  })

  it('tells a member to ask the owner instead of showing buttons the server would refuse', () => {
    state.admin = false
    for (const action of ['install_whisper', 'install_gpu_torch', 'diagnostics', 'settings'] as const) {
      const out = html(need({ action, packages: ['faster_whisper'] }))
      expect(out).toContain(ASK_OWNER)
      expect(out).not.toContain('Open Diagnostics')
      expect(out).not.toContain('Add in Settings')
      expect(out).not.toContain('Install…')
    }
  })

  it('away from the main PC an admin is told where installing is done', () => {
    state.pc = 'remote'
    expect(html(need({ action: 'install_whisper' }))).toContain('Installing is done on the main PC.')
    expect(html(need({ action: 'settings' }))).toContain('Add it on the main PC.')
  })

  it('keeps Check again for a program Baihe cannot install, for anyone', () => {
    state.admin = false
    expect(html(need({ id: 'ffmpeg', action: 'recheck' }))).toContain('Check again')
  })

  it('shows only the link for a separate program', () => {
    state.admin = false
    const out = html(need({ id: 'ollama', action: 'external', link: { href: 'https://ollama.com/download', label: 'Get Ollama' } }))
    expect(out).toContain('Get Ollama')
    expect(out).not.toContain(ASK_OWNER)
  })
})
