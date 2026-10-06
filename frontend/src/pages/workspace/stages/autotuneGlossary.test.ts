import { describe, expect, it } from 'vitest'

import { ApiError } from '../../../api/client'
import type { NovelGlossaryProposal } from '../../../types/autotuneGlossary'
import {
  autotuneEta,
  formatElapsed,
  AUTOTUNE_EXPIRED,
  ENGINE_CHANGED_TEXT,
  PAID_ENGINE_TEXT,
  addTermsLabel,
  applySummary,
  autotuneApplyErrorText,
  autotuneBlocker,
  autotuneProgressText,
  chosenTerms,
  countInGlossary,
  GLOSSARY_EXPIRED,
  novelGlossaryApplyErrorText,
  overwriteConfirmText,
  defaultTermSelection,
  highConfidenceTerms,
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
  occurrences: 3,
  alternatives: [],
  confidence: 'high',
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
    // A term no longer proposed is never sent.
    expect(chosenTerms(props, new Set(['zz', 'a']))).toEqual(['a'])
  })
  it('counts overwrites against the glossary as it is now, not the run snapshot', () => {
    // 'a' was new when the run finished but has been added since; 'd' was removed.
    expect(countInGlossary(['a', 'd'], ['a ', 'x'])).toBe(1)
    expect(overwriteConfirmText(1)).toBe('Replace 1 existing term in the series glossary?')
    expect(overwriteConfirmText(2)).toBe('Replace 2 existing terms in the series glossary?')
    expect(overwriteConfirmText(null)).toBe('This may replace existing terms in the series glossary.')
    expect(overwriteConfirmText(0)).toMatch(/will be added/)
  })
  it('maps a lost-results apply 400 to "run again"', () => {
    expect(novelGlossaryApplyErrorText(new ApiError(400, { code: 'unsupported_operation', message: 'x' }))).toBe(GLOSSARY_EXPIRED)
    expect(novelGlossaryApplyErrorText(new ApiError(422, { code: 'validation_error', message: 'x' }))).toBeNull()
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

describe('auto-tune elapsed time and ETA', () => {
  it('formats elapsed time as a clock', () => {
    expect(formatElapsed(5)).toBe('0:05')
    expect(formatElapsed(125)).toBe('2:05')
    expect(formatElapsed(3725)).toBe('1:02:05')
    expect(formatElapsed(-3)).toBe('0:00')
  })
  it('has no estimate until a candidate has finished', () => {
    expect(autotuneEta(30, 'Testing candidate 1 of 3 (500ms)...')).toBeNull()
    expect(autotuneEta(30, 'Starting')).toBeNull()
  })
  it('extrapolates from the finished candidates', () => {
    // 2 of 3 started after 60 s: one finished (60 s each), two remain incl. the current one.
    expect(autotuneEta(60, 'Testing candidate 2 of 3 (800ms)...')).toBe('about 2:00 left')
    expect(autotuneEta(180, 'Testing candidate 3 of 3 (800ms)...')).toBe('about 1:30 left')
  })
})

describe('highConfidenceTerms', () => {
  it('keeps High proposals that are not already in the glossary', () => {
    const low = { ...p('low'), confidence: 'low' as const }
    expect([...highConfidenceTerms([p('a'), low, p('b', true), p('c')])]).toEqual(['a', 'c'])
    expect(highConfidenceTerms([]).size).toBe(0)
  })
})
