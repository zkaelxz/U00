import { beforeEach, describe, expect, it, vi } from 'vitest'

import { narrationApi } from '../../api/dub'
import { probeJob } from '../../api/jobs'
import { startNovelUrlImport } from '../../api/sourcesExtraction'
import { addChaptersFromSources } from '../../api/workspace'
import { addChaptersFromLink } from './novelLinkActions'
import { EMPTY_TAG_CHOICE } from './stages/TagChoices'

vi.mock('../../api/dub', async (orig) => ({ ...(await orig<object>()), narrationApi: { config: vi.fn(), run: vi.fn(), estimate: vi.fn() } }))
vi.mock('../../api/jobs', async (orig) => ({ ...(await orig<object>()), probeJob: vi.fn() }))
vi.mock('../../api/sourcesExtraction', async (orig) => ({ ...(await orig<object>()), startNovelUrlImport: vi.fn() }))
vi.mock('../../api/workspace', async (orig) => ({ ...(await orig<object>()), addChaptersFromSources: vi.fn() }))

const estimate = (over: object) => ({
  engine: 'claude', model: 'm', chunk_count: 10, chapters: 1, estimated_usd: 0, has_key: true,
  cap_applies: false, monthly_cap_set: false, monthly_refusal: false, estimate_above_cap: false, ...over,
})

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(startNovelUrlImport).mockResolvedValue({ job_id: 'imp' } as never)
  vi.mocked(probeJob).mockResolvedValue({ status: 'done' } as never)
  vi.mocked(narrationApi.config).mockResolvedValue({ engines: [{ key: 'claude', key_configured: true }], default_engine: 'claude' } as never)
  vi.mocked(narrationApi.run).mockResolvedValue({ job_id: 'tag' })
  vi.mocked(narrationApi.estimate).mockResolvedValue(estimate({}) as never)
})

describe('addChaptersFromLink', () => {
  it('imports, appends the new text, then adds lines for just the new text', async () => {
    vi.mocked(addChaptersFromSources).mockResolvedValue({ char_count: 90, added_chars: 40, pending_chars: 42 })
    const steps: string[] = []
    expect(await addChaptersFromLink(5, 'https://x/2', (s) => steps.push(s), 3)).toBe(42)
    expect(steps).toEqual(['import', 'text', 'lines'])
    expect(startNovelUrlImport).toHaveBeenCalledWith('https://x/2', 5, {})
    expect(addChaptersFromSources).toHaveBeenCalledWith(5, 4)
    expect(narrationApi.run).toHaveBeenCalledWith(5, { engine: 'claude', append: true })
  })

  it('tags with the engine the person picked, not the first one with a key', async () => {
    vi.mocked(narrationApi.config).mockResolvedValue({
      engines: [{ key: 'claude', key_configured: true }, { key: 'ollama', key_configured: true }], default_engine: 'claude',
    } as never)
    vi.mocked(addChaptersFromSources).mockResolvedValue({ char_count: 90, added_chars: 40, pending_chars: 42 })
    await addChaptersFromLink(5, 'https://x/2', () => {}, 3, { ...EMPTY_TAG_CHOICE, engine: 'ollama', model: 'qwen', from: '2', to: '3' })
    expect(narrationApi.run).toHaveBeenCalledTimes(1)
    expect(narrationApi.run).toHaveBeenCalledWith(5, { engine: 'ollama', model: 'qwen', append: true })
    expect(narrationApi.estimate).toHaveBeenCalledWith(5, { engine: 'ollama', model: 'qwen', append: true })
  })

  it('one narrator adds lines with no estimate and no AI', async () => {
    vi.mocked(addChaptersFromSources).mockResolvedValue({ char_count: 90, added_chars: 40, pending_chars: 42 })
    await addChaptersFromLink(5, 'https://x/2', () => {}, 3, { ...EMPTY_TAG_CHOICE, oneNarrator: true })
    expect(narrationApi.estimate).not.toHaveBeenCalled()
    expect(narrationApi.run).toHaveBeenCalledWith(5, { engine: 'claude', one_narrator: true, append: true })
  })

  it('stops a paid run with no monthly cap until the cost is accepted', async () => {
    vi.mocked(narrationApi.estimate).mockResolvedValue(estimate({ cap_applies: true, estimated_usd: 1.4 }) as never)
    vi.mocked(addChaptersFromSources).mockResolvedValue({ char_count: 90, added_chars: 40, pending_chars: 42 })
    await expect(addChaptersFromLink(5, 'https://x/2', () => {}, 3)).rejects.toThrow(/no monthly spending cap/)
    expect(narrationApi.run).not.toHaveBeenCalled()
    await addChaptersFromLink(5, 'https://x/2', () => {}, 3, EMPTY_TAG_CHOICE, () => true)
    expect(narrationApi.run).toHaveBeenCalledTimes(1)
  })

  it('does not touch the lines when nothing new was added', async () => {
    vi.mocked(addChaptersFromSources).mockResolvedValue({ char_count: 50, added_chars: 0, pending_chars: 0 })
    expect(await addChaptersFromLink(5, 'https://x/2', () => {})).toBe(0)
    expect(narrationApi.run).not.toHaveBeenCalled()
  })

  it('finishes the lines an earlier attempt left when this attempt adds nothing', async () => {
    vi.mocked(addChaptersFromSources).mockResolvedValue({ char_count: 90, added_chars: 0, pending_chars: 42 })
    expect(await addChaptersFromLink(5, 'https://x/2', () => {})).toBe(42)
    expect(narrationApi.run).toHaveBeenCalledWith(5, { engine: 'claude', append: true })
  })

  it('stops with the server refusal when the translation text was edited', async () => {
    vi.mocked(addChaptersFromSources).mockRejectedValue(new Error('not a copy'))
    await expect(addChaptersFromLink(5, 'https://x/2', () => {})).rejects.toThrow('not a copy')
    expect(narrationApi.run).not.toHaveBeenCalled()
  })
})
