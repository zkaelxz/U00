import { describe, expect, it, vi } from 'vitest'

import type { ReviewMatch } from '../../../../types/review'
import { applySameFix, findSameFix, literalReplacement, sameFixOf, wholeWordPattern } from './sameFix'

vi.mock('../../../../api/review', () => ({ previewFindReplace: vi.fn(), applyFindReplace: vi.fn() }))
vi.mock('../../../../api/translateStage', () => ({ saveWordReplacement: vi.fn() }))

describe('sameFixOf', () => {
  it('finds the one wording that changed, as whole words', () => {
    expect(sameFixOf('Lin went home.', 'Rin went home.')).toEqual({ find: 'Lin', replace: 'Rin' })
    expect(sameFixOf('Thank you, Senior Brother.', 'Thank you, Shixiong.')).toEqual({ find: 'Senior Brother', replace: 'Shixiong' })
  })

  it('offers nothing for a grammar word, which changes with the line around it', () => {
    expect(sameFixOf('She is late', 'She was late')).toBeNull()
    expect(sameFixOf('He left.', 'She left.')).toBeNull()
  })

  it('takes the same word fixed twice in a line as one fix', () => {
    expect(sameFixOf('Lin met Lin.', 'Rin met Rin.')).toEqual({ find: 'Lin', replace: 'Rin' })
  })

  it('takes two nearby fixes as one phrase', () => {
    expect(sameFixOf('Lin met Wu.', 'Rin met Wen.')).toEqual({ find: 'Lin met Wu', replace: 'Rin met Wen' })
  })

  it('offers nothing for an insertion, a deletion, a rewrite or one letter', () => {
    expect(sameFixOf('He left.', 'He left early.')).toBeNull()
    expect(sameFixOf('Lin-ge said so', 'Lin said so')).toBeNull()
    expect(sameFixOf('I go.', 'We go.')).toBeNull()
    expect(sameFixOf('same', 'same')).toBeNull()
    expect(sameFixOf('A '.repeat(30) + 'end', 'B '.repeat(50) + 'end')).toBeNull()
  })
})

describe('the preview pattern', () => {
  it('escapes the text and matches whole words only', () => {
    const pattern = wholeWordPattern('Mr. Wu (2)')
    expect(pattern).toBe('(?<!\\w)Mr\\. Wu \\(2\\)(?!\\w)')
    const js = new RegExp(pattern, 'u')
    expect(js.test('ask Mr. Wu (2) now')).toBe(true)
    expect(js.test('ask Mr. Wu (2)x')).toBe(false)
  })

  it('keeps a backslash in the replacement as written', () => {
    expect(literalReplacement('a\\1b')).toBe('a\\\\1b')
  })
})

const match = (id: number): ReviewMatch => ({ id, idx: id - 1, old_text: `Lin ${id}`, new_text: `Rin ${id}` })

describe('findSameFix', () => {
  it('asks for whole-word, case-matched lines and leaves out the line just fixed', async () => {
    const preview = vi.fn().mockResolvedValue([match(3), match(5), match(9)])
    const offer = await findSameFix(7, 5, 'Lin left.', 'Rin left.', preview)
    expect(preview).toHaveBeenCalledWith(7, { find: '(?<!\\w)Lin(?!\\w)', replace: 'Rin', case_sensitive: true, use_regex: true })
    expect(offer).toEqual({ fix: { find: 'Lin', replace: 'Rin' }, matches: [match(3), match(9)] })
  })

  it('asks nothing for an edit that is not one fix, and offers nothing when no other line has it', async () => {
    const preview = vi.fn().mockResolvedValue([match(5)])
    expect(await findSameFix(7, 5, 'He left.', 'He left early.', preview)).toBeNull()
    expect(preview).not.toHaveBeenCalled()
    expect(await findSameFix(7, 5, 'Lin left.', 'Rin left.', preview)).toBeNull()
  })
})

describe('applySameFix', () => {
  const offer = { fix: { find: 'Lin', replace: 'Rin' }, matches: [match(3), match(9)] }

  it('applies the previewed lines and says how many changed', async () => {
    const apply = vi.fn().mockResolvedValue({ applied: 1, stale: 1, applied_ids: [3], stale_ids: [9] })
    const saveReplacement = vi.fn()
    const done = await applySameFix(7, offer, false, { apply, saveReplacement })
    expect(apply).toHaveBeenCalledWith(7, offer.matches)
    expect(saveReplacement).not.toHaveBeenCalled()
    expect(done).toEqual({ text: 'Fixed 1 more line; 1 changed meanwhile and was left.', changed: true })
  })

  it('"always" also saves the fix as a series word replacement', async () => {
    const apply = vi.fn().mockResolvedValue({ applied: 2, stale: 0, applied_ids: [3, 9], stale_ids: [] })
    const saveReplacement = vi.fn().mockResolvedValue({})
    const done = await applySameFix(7, offer, true, { apply, saveReplacement })
    expect(saveReplacement).toHaveBeenCalledWith(7, { find_text: 'Lin', replace_text: 'Rin' })
    expect(apply.mock.invocationCallOrder[0]).toBeLessThan(saveReplacement.mock.invocationCallOrder[0])
    expect(done.text).toBe('Fixed 2 more lines. New translations in this series get the same fix.')
  })

  it('"always" saves nothing when no line took the fix, or it changes capitals only', async () => {
    const saveReplacement = vi.fn()
    const none = vi.fn().mockResolvedValue({ applied: 0, stale: 2, applied_ids: [], stale_ids: [3, 9] })
    expect((await applySameFix(7, offer, true, { apply: none, saveReplacement })).text)
      .toBe('Fixed 0 more lines; 2 changed meanwhile and were left.')
    const capitals = { ...offer, fix: { find: 'lin', replace: 'Lin' } }
    const both = vi.fn().mockResolvedValue({ applied: 2, stale: 0, applied_ids: [3, 9], stale_ids: [] })
    expect((await applySameFix(7, capitals, true, { apply: both, saveReplacement })).text).toBe('Fixed 2 more lines.')
    expect(saveReplacement).not.toHaveBeenCalled()
  })

  it('passes on why nothing was applied, keeping the offer and saving no rule', async () => {
    const apply = vi.fn().mockResolvedValue('Save or discard your edit to #4 first.')
    const saveReplacement = vi.fn()
    expect(await applySameFix(7, offer, true, { apply, saveReplacement }))
      .toEqual({ text: 'Save or discard your edit to #4 first.', changed: false, blocked: true })
    expect(saveReplacement).not.toHaveBeenCalled()
  })
})
