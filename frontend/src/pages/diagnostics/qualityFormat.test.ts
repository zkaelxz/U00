import { describe, expect, it } from 'vitest'

import type { QualityCheck, QualityClip } from '../../types/diagnostics'
import { clipStatus, differenceText, marginText, metricLabel, nextStep, sortedClips, verdict, verdictDetail } from './qualityFormat'

const clip = (over: Partial<QualityClip> = {}): QualityClip => ({
  id: 'title59-transcription', kind: 'transcription', metric: 'cer', score: 0.655, baseline: 0.642,
  status: 'pass', failed: false, runs: 5, fixed_decoding: true, ...over,
})

const check = (over: Partial<QualityCheck> = {}): QualityCheck => ({
  found: true, unreadable: false, when: '2026-11-22 10:15', age_days: 2, fresh: true, passed: true,
  machine: 'V100', gpu: 'Tesla V100', repeats: 5, margin: 0.02, max_age_days: 7, clips: [clip()], ...over,
})

const worse = clip({ id: 'title60-transcription', score: 0.611, baseline: 0.652, status: 'fail' })

describe('quality card', () => {
  it('writes the difference in words, and a lower speaker error counts as better', () => {
    expect(differenceText(worse)).toBe('0.041 worse')
    expect(differenceText(clip())).toBe('0.013 better')
    expect(differenceText(clip({ metric: 'der', kind: 'diarization', score: 0.418, baseline: 0.421 }))).toBe('0.003 better')
    expect(differenceText(clip({ metric: 'der', score: 0.45, baseline: 0.421 }))).toBe('0.029 worse')
    expect(differenceText(clip({ score: 0.642 }))).toBe('Same')
    expect(differenceText(clip({ failed: true, score: null, status: 'fail' }))).toBe('Failed to run')
    expect(differenceText(clip({ baseline: null, status: 'none' }))).toBe('No baseline')
  })

  it('labels each row OK or Worse', () => {
    expect(clipStatus(clip())).toEqual({ tone: 'ok', label: 'OK' })
    expect(clipStatus(worse)).toEqual({ tone: 'bad', label: 'Worse' })
    expect(clipStatus(clip({ status: 'fail', failed: true, score: null })).label).toBe('Failed')
    expect(clipStatus(clip({ status: 'none', baseline: null })).label).toBe('No baseline')
  })

  it('puts the worse rows first and keeps the file order otherwise', () => {
    const a = clip({ id: 'a' })
    const b = clip({ id: 'b', status: 'none', baseline: null })
    const c = clip({ id: 'c', status: 'fail' })
    const d = clip({ id: 'd' })
    expect(sortedClips([a, b, c, d]).map((x) => x.id)).toEqual(['c', 'b', 'a', 'd'])
  })

  it('names the metric in plain words', () => {
    expect(metricLabel(clip({ metric: 'der', kind: 'diarization' }))).toBe('share of speech given to the wrong person')
    expect(metricLabel(clip({ metric: null, kind: 'translation' }))).toBe('translation')
  })

  it('leads with a verdict: worse first, then no baseline, too old, ready', () => {
    expect(verdict(check({ passed: false, clips: [clip(), worse, clip()] }))).toEqual({ tone: 'bad', title: 'Not ready for a release: 1 of 3 checks got worse' })
    expect(verdict(check({ passed: false, clips: [clip({ status: 'fail', failed: true, score: null })] })).title).toContain('did not pass')
    expect(verdict(check({ passed: false, clips: [clip({ status: 'none', baseline: null })] })).title).toContain('no check has a baseline')
    expect(verdict(check({ fresh: false, age_days: 9 }))).toEqual({ tone: 'warn', title: 'Passed, but too old for a release' })
    expect(verdict(check())).toEqual({ tone: 'ok', title: 'Ready for a release: all 1 check is OK' })
    expect(verdict(check({ found: false })).title).toBe('No check yet')
    expect(verdict(check({ unreadable: true })).tone).toBe('bad')
  })

  it('says when it ran and how many days it stays valid', () => {
    expect(verdictDetail(check())).toBe('Checked 2 days ago on V100 (Tesla V100), middle of 5 runs. A check counts for a release for 7 days: 5 left.')
    expect(verdictDetail(check({ age_days: 0, machine: null, repeats: 1 }))).toBe('Checked today. A check counts for a release for 7 days: 7 left.')
    expect(verdictDetail(check({ age_days: 9, fresh: false }))).toContain('this one is older')
    expect(marginText(check())).toContain('up to 0.02 either way')
  })

  it('offers a next step only when there is something to do', () => {
    expect(nextStep(check())).toBeNull()
    expect(nextStep(check({ passed: false, clips: [worse] }))).toContain('Run the check again')
    expect(nextStep(check({ found: false }))).toContain('tools.release_check')
    expect(nextStep(check({ fresh: false }))).toBe('Run the check again before a release.')
  })
})
