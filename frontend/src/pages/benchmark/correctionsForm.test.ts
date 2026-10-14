import { describe, expect, it } from 'vitest'

import type { AddCorrectionsResult, BenchmarkSet, TitleCorrections } from '../../api/benchmark'
import { NEW_SET, chosenSetName, correctionProblems, describeAdd, describeStats, transcriptionSetNames } from './correctionsForm'

const stats = (over: Partial<TitleCorrections> = {}): TitleCorrections => ({
  drama_id: 7, raw_available: true, lines_compared: 200, lines_corrected: 14, raw_to_edited_cer: 0.084, ...over,
})
const added = (over: Partial<AddCorrectionsResult> = {}): AddCorrectionsResult => ({
  set_name: 'moon', lines_compared: 200, lines_corrected: 14, unedited_skipped: 186, unclippable_skipped: 0,
  case_count: 14, remaining: 0, added: 14, skipped: 0, dry_run: false, ...over,
})
const set = (stage: string, name: string): BenchmarkSet => ({ stage, tier: 'application', set_name: name, case_count: 1, with_reference: 1 })

describe('corrections form', () => {
  it('lists only sets that hold transcription cases, once each', () => {
    expect(transcriptionSetNames([set('translation', 'a'), set('transcription', 'z'), set('transcription', 'b'), set('transcription', 'b'), set('transcription', '')])).toEqual(['b', 'z'])
  })

  it('uses the typed name only for a new set', () => {
    expect(chosenSetName('moon', 'ignored')).toBe('moon')
    expect(chosenSetName(NEW_SET, '  fresh ')).toBe('fresh')
  })

  it('explains why it cannot be sent', () => {
    expect(correctionProblems(null, '', null)).toEqual(['Pick a title.', 'Choose a set or give a new one a name.'])
    expect(correctionProblems(7, 'moon', stats({ lines_corrected: 0 }))[0]).toContain('No corrected lines')
    expect(correctionProblems(7, 'moon', stats({ raw_available: false }))[0]).toContain('no original transcription')
    expect(correctionProblems(7, 'moon', stats())).toEqual([])
  })

  it('describes the title in plain words', () => {
    expect(describeStats(stats())).toBe('14 of 200 lines corrected. The raw transcript was 8.4% off your text.')
    expect(describeStats(stats({ lines_corrected: 0, lines_compared: 1, raw_to_edited_cer: null }))).toBe('0 of 1 line corrected.')
    expect(describeStats(stats({ raw_available: false }))).toContain('No original transcription')
  })

  it('says when lines are left for another add', () => {
    expect(describeAdd(added({ added: 100, case_count: 100, remaining: 30 }))).toContain('30 lines left: add again for the rest (at most 100 per add).')
  })

  it('says unedited lines are skipped, in a preview and after adding', () => {
    expect(describeAdd(added({ dry_run: true }))).toBe('This would add 14 clips to “moon”. 186 unedited lines skipped.')
    expect(describeAdd(added({ skipped: 1, added: 13, unedited_skipped: 1 }))).toBe(
      'Added 13 clips to “moon”. 1 line already in the set was skipped. 1 unedited line skipped.',
    )
  })
})
