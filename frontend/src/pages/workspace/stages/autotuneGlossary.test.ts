import { describe, expect, it } from 'vitest'

import { ApiError } from '../../../api/client'
import type { NovelGlossaryProposal } from '../../../types/autotuneGlossary'
import {
  AUTOTUNE_EXPIRED,
  ENGINE_CHANGED_TEXT,
  PAID_ENGINE_TEXT,
  addTermsLabel,
  applySummary,
  autotuneApplyErrorText,
  autotuneBlocker,
  autotuneProgressText,
  chosenTerms,
  countExisting,
  defaultTermSelection,
  isActiveStatus,
  novelGlossaryBlocker,
  novelGlossaryProgressText,
  novelGlossaryStartErrorText,
  toggleTerm,
} from './autotuneGlossary'

const p = (term: string, already = false): NovelGlossaryProposal => ({
  term,
  suggested_translation: `${term}-en`,
  category: null,
  policy: null,
  reason: '',
  already_in_glossary: already,
})

describe('auto-tune helpers', () => {
  it('reads the job message as "Testing 2 of 3 (800 ms)…"', () => {
    expect(autotuneProgressText('running', 'Testing candidate 2 of 3 (800ms)...')).toBe('Testing 2 of 3 (800 ms)…')
    expect(autotuneProgressText('running', '')).toBe('Testing…')
    expect(autotuneProgressText('queued', '')).toBe('Waiting for the GPU…')
  })
  it('explains why Start is disabled', () => {
    expect(autotuneBlocker(false, false)).toBe('Still needed: audio on this drama.')
    expect(autotuneBlocker(true, true)).toBe('Wait for the running job to finish.')
    expect(autotuneBlocker(true, false)).toBeNull()
  })
  it('maps a lost-results apply error to "run again"', () => {
    expect(autotuneApplyErrorText(new ApiError(400, { code: 'unsupported_operation', message: 'x' }))).toBe(AUTOTUNE_EXPIRED)
    expect(autotuneApplyErrorText(new ApiError(422, { code: 'validation_error', message: 'x' }))).toBe(AUTOTUNE_EXPIRED)
    expect(autotuneApplyErrorText(new ApiError(404, { code: 'not_found', message: 'x' }))).toBeNull()
  })
  it('knows active statuses', () => {
    expect(isActiveStatus('queued')).toBe(true)
    expect(isActiveStatus('running')).toBe(true)
    expect(isActiveStatus('done')).toBe(false)
    expect(isActiveStatus(null)).toBe(false)
  })
})

describe('glossary-from-novel helpers', () => {
  it('needs a series first, then novel text, each with a Source link', () => {
    expect(novelGlossaryBlocker(2, null, true)).toEqual({
      text: 'Still needed: a series for this drama',
      link: 'set it in Details',
      href: '#/drama/2/source',
    })
    expect(novelGlossaryBlocker(2, 7, false)?.link).toBe('attach it on Source')
    expect(novelGlossaryBlocker(2, 7, true)).toBeNull()
  })
  it('shows progress as a percent', () => {
    expect(novelGlossaryProgressText('running', 0.424)).toBe('Reading the novel… 42%')
    expect(novelGlossaryProgressText('running', null)).toBe('Reading the novel…')
  })
  it('maps 403 and 409 on start to the spec copy', () => {
    expect(novelGlossaryStartErrorText(new ApiError(403, { code: 'forbidden', message: 'x' }))).toBe(PAID_ENGINE_TEXT)
    expect(novelGlossaryStartErrorText(new ApiError(409, { code: 'conflict', message: 'x' }))).toBe(ENGINE_CHANGED_TEXT)
    expect(novelGlossaryStartErrorText(new ApiError(503, { code: 'dependency_unavailable', message: 'x' }))).toBeNull()
  })
  it('selects new terms by text; unchecking 1 of 3 gives "Add 2 terms"', () => {
    const props = [p('a'), p('b'), p('c'), p('d', true)]
    const sel = defaultTermSelection(props)
    expect(chosenTerms(props, sel)).toEqual(['a', 'b', 'c'])
    const less = toggleTerm(sel, 'b')
    expect(chosenTerms(props, less)).toEqual(['a', 'c'])
    expect(addTermsLabel(chosenTerms(props, less).length)).toBe('Add 2 terms to series glossary')
    expect(addTermsLabel(1)).toBe('Add 1 term to series glossary')
    expect(countExisting(props, ['a', 'd'])).toBe(1)
    // A term no longer proposed is never sent.
    expect(chosenTerms(props, new Set(['zz', 'a']))).toEqual(['a'])
  })
  it('summarises an apply result', () => {
    expect(applySummary({ added: ['a', 'b'], overwritten: ['d'], skipped_existing: [], unknown: [] })).toBe(
      'Added 2. Overwrote 1.',
    )
    expect(applySummary({ added: [], overwritten: [], skipped_existing: ['d'], unknown: ['z'] })).toBe(
      'Added 0. Skipped 1 already in the glossary. 1 no longer proposed.',
    )
  })
})
