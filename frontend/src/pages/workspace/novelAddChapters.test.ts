import { beforeEach, describe, expect, it, vi } from 'vitest'

import { narrationApi } from '../../api/dub'
import { probeJob } from '../../api/jobs'
import { startNovelUrlImport } from '../../api/sourcesExtraction'
import { addChaptersFromSources } from '../../api/workspace'
import { addChaptersFromLink } from './novelLinkActions'

vi.mock('../../api/dub', async (orig) => ({ ...(await orig<object>()), narrationApi: { config: vi.fn(), run: vi.fn() } }))
vi.mock('../../api/jobs', async (orig) => ({ ...(await orig<object>()), probeJob: vi.fn() }))
vi.mock('../../api/sourcesExtraction', async (orig) => ({ ...(await orig<object>()), startNovelUrlImport: vi.fn() }))
vi.mock('../../api/workspace', async (orig) => ({ ...(await orig<object>()), addChaptersFromSources: vi.fn() }))

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(startNovelUrlImport).mockResolvedValue({ job_id: 'imp' } as never)
  vi.mocked(probeJob).mockResolvedValue({ status: 'done' } as never)
  vi.mocked(narrationApi.config).mockResolvedValue({ engines: [{ key: 'claude', key_configured: true }], default_engine: 'claude' } as never)
  vi.mocked(narrationApi.run).mockResolvedValue({ job_id: 'tag' })
})

describe('addChaptersFromLink', () => {
  it('imports, appends the new text, then adds lines for just the new text', async () => {
    vi.mocked(addChaptersFromSources).mockResolvedValue({ char_count: 90, added_chars: 40 })
    const steps: string[] = []
    expect(await addChaptersFromLink(5, 'https://x/2', (s) => steps.push(s), 3)).toBe(40)
    expect(steps).toEqual(['import', 'text', 'lines'])
    expect(startNovelUrlImport).toHaveBeenCalledWith('https://x/2', 5, {})
    expect(addChaptersFromSources).toHaveBeenCalledWith(5, 4)
    expect(narrationApi.run).toHaveBeenCalledWith(5, { engine: 'claude', append_chars: 40 })
  })

  it('does not touch the lines when nothing new was added', async () => {
    vi.mocked(addChaptersFromSources).mockResolvedValue({ char_count: 50, added_chars: 0 })
    expect(await addChaptersFromLink(5, 'https://x/2', () => {})).toBe(0)
    expect(narrationApi.run).not.toHaveBeenCalled()
  })

  it('stops with the server refusal when the translation text was edited', async () => {
    vi.mocked(addChaptersFromSources).mockRejectedValue(new Error('not a copy'))
    await expect(addChaptersFromLink(5, 'https://x/2', () => {})).rejects.toThrow('not a copy')
    expect(narrationApi.run).not.toHaveBeenCalled()
  })
})
