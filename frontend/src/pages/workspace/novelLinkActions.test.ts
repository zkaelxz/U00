import { beforeEach, describe, expect, it, vi } from 'vitest'

import { applyNovelGlossary, getNovelGlossary } from '../../api/autotuneGlossary'
import { getTranslateConfig, startTranslateRun } from '../../api/translateStage'
import { translateWithoutReview } from './novelLinkActions'
import * as form from './translateForm'

vi.mock('../../api/autotuneGlossary', async (orig) => ({ ...(await orig<object>()), applyNovelGlossary: vi.fn(), getNovelGlossary: vi.fn() }))
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
