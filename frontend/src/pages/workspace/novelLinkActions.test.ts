import { beforeEach, describe, expect, it, vi } from 'vitest'

import { applyNovelGlossary, getNovelGlossary } from '../../api/autotuneGlossary'
import { getTranslateConfig, startTranslateRun } from '../../api/translateStage'
import { narrationApi } from '../../api/dub'
import { novelLinkActions, StepFailed, translateWithoutReview } from './novelLinkActions'
import { EMPTY_TAG_CHOICE } from './stages/TagChoices'
import * as form from './translateForm'

vi.mock('../../api/autotuneGlossary', async (orig) => ({ ...(await orig<object>()), applyNovelGlossary: vi.fn(), getNovelGlossary: vi.fn() }))
vi.mock('../../api/dub', async (orig) => ({
  ...(await orig<object>()),
  narrationApi: { config: vi.fn(), estimate: vi.fn(), run: vi.fn() },
}))
vi.mock('../../api/jobs', async (orig) => ({ ...(await orig<object>()), probeJob: vi.fn(async () => ({ status: 'done' })) }))
vi.mock('../../api/translateStage', async (orig) => ({ ...(await orig<object>()), getTranslateConfig: vi.fn(), startTranslateRun: vi.fn() }))
vi.mock('./translateForm', async (orig) => ({
  ...(await orig<object>()),
  initialForm: vi.fn(() => ({})),
  applyStartToForm: vi.fn((f: unknown) => f),
  loadPresetStart: vi.fn(() => ({})),
  buildRunBody: vi.fn(() => ({ locale: 'us' })),
}))

const term = (t: string, already: boolean, translation: string) =>
  ({ term: t, already_in_glossary: already, suggested_translation: translation })

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getTranslateConfig).mockResolvedValue({ translate_start: null } as never)
  vi.mocked(startTranslateRun).mockResolvedValue({ job_id: 'j1' } as never)
})

describe('translateWithoutReview', () => {
  it('adds only new terms that have a translation, then starts the run with the form body', async () => {
    vi.mocked(getNovelGlossary).mockResolvedValue({
      run_id: 'r1',
      proposals: [term('甲', false, 'Jia'), term('乙', true, 'Yi'), term('丙', false, '  ')],
    } as never)
    expect(await translateWithoutReview(5, true)).toBe('j1')
    expect(applyNovelGlossary).toHaveBeenCalledWith(5, { terms: ['甲'], run_id: 'r1' })
    expect(startTranslateRun).toHaveBeenCalledWith(5, { locale: 'us' })
    expect(form.buildRunBody).toHaveBeenCalled()
  })

  it('adds nothing for a title without a series but still translates', async () => {
    expect(await translateWithoutReview(5, false)).toBe('j1')
    expect(getNovelGlossary).not.toHaveBeenCalled()
    expect(applyNovelGlossary).not.toHaveBeenCalled()
    expect(startTranslateRun).toHaveBeenCalled()
  })

  it('skips the apply call when no term qualifies', async () => {
    vi.mocked(getNovelGlossary).mockResolvedValue({ run_id: 'r1', proposals: [term('乙', true, 'Yi')] } as never)
    await translateWithoutReview(5, true)
    expect(applyNovelGlossary).not.toHaveBeenCalled()
  })
})

const narrationCfg = { default_engine: 'claude', engines: [{ key: 'claude', key_configured: true }, { key: 'ollama', key_configured: true }] }
const estimate = (over: object) => ({
  engine: 'ollama', model: 'm', chunk_count: 10, chapters: 1, estimated_usd: 0, has_key: true,
  cap_applies: false, monthly_cap_set: false, monthly_refusal: false, estimate_above_cap: false, ...over,
})

describe('the Continue chain tag step', () => {
  beforeEach(() => {
    vi.mocked(narrationApi.config).mockResolvedValue(narrationCfg as never)
    vi.mocked(narrationApi.run).mockResolvedValue({ job_id: 'n1' })
  })

  it('uses the engine, model and chapters the person picked, not the first engine with a key', async () => {
    vi.mocked(narrationApi.estimate).mockResolvedValue(estimate({}) as never)
    const tag = { ...EMPTY_TAG_CHOICE, engine: 'ollama', model: 'qwen', from: '2', to: '4' }
    await novelLinkActions(5, '', tag, () => false).tag()
    expect(narrationApi.run).toHaveBeenCalledTimes(1)
    expect(narrationApi.run).toHaveBeenCalledWith(5, { engine: 'ollama', model: 'qwen', chapter_from: 2, chapter_to: 4 })
    expect(narrationApi.estimate).toHaveBeenCalledWith(5, expect.objectContaining({ engine: 'ollama' }))
  })

  it('names the default engine when none was changed, so the shown engine is the one that runs', async () => {
    vi.mocked(narrationApi.estimate).mockResolvedValue(estimate({ engine: 'claude' }) as never)
    await novelLinkActions(5, '', EMPTY_TAG_CHOICE, () => false).tag()
    expect(vi.mocked(narrationApi.run).mock.calls[0][1]).toEqual({ engine: 'claude' })
  })

  it('one narrator asks for no estimate and no AI', async () => {
    await novelLinkActions(5, '', { ...EMPTY_TAG_CHOICE, engine: 'ollama', oneNarrator: true }, () => false).tag()
    expect(narrationApi.estimate).not.toHaveBeenCalled()
    expect(vi.mocked(narrationApi.run).mock.calls[0][1]).toMatchObject({ one_narrator: true })
  })

  it('stops before a paid run with no monthly cap until the cost is accepted', async () => {
    vi.mocked(narrationApi.estimate).mockResolvedValue(estimate({ engine: 'claude', cap_applies: true, estimated_usd: 3.2 }) as never)
    await expect(novelLinkActions(5, '', EMPTY_TAG_CHOICE, () => false).tag()).rejects.toBeInstanceOf(StepFailed)
    expect(narrationApi.run).not.toHaveBeenCalled()
    await novelLinkActions(5, '', EMPTY_TAG_CHOICE, () => true).tag()
    expect(narrationApi.run).toHaveBeenCalledTimes(1)
  })

  it('translates with the same engine, model and chapters', async () => {
    const f: Record<string, unknown> = {}
    vi.mocked(form.initialForm).mockReturnValue(f as never)
    await translateWithoutReview(5, false, { ...EMPTY_TAG_CHOICE, engine: 'ollama', model: 'qwen', from: '1', to: '1' })
    expect(f).toMatchObject({ engine: 'ollama', model: 'qwen', chapter_from: '1', chapter_to: '1' })
  })
})
