import { describe, expect, it } from 'vitest'

import { buildGlossaryAffectedQuery, getGlossaryAffected, startGlossaryAffectedRun } from '../../../api/translateStage'
import type { GlossaryAffectedLine, GlossaryAffectedPreview, TranslateRunEstimate } from '../../../types/translateStage'
import type { RunForm } from '../translateForm'
import {
  affectedParams,
  buildAffectedRunBody,
  chosenIds,
  costText,
  defaultSelection,
  selectionEstimate,
} from './glossaryRetranslate'

const line = (id: number, hand_edited: boolean): GlossaryAffectedLine => ({
  id, idx: id - 1, start: 0, end: 1, zh: '林晚', en: 'Lin', hand_edited,
  matched_terms: [{ term_id: 1, term_original: '林晚', term_translation: 'Lin Wan', reason: 'source' }],
})
const est = (usd: number | null, n: number): TranslateRunEstimate => ({
  engine: 'claude', model: null, estimated_usd: usd, target_line_count: n, free: false,
  cap_applies: true, effective_cap_usd: null, monthly_refusal: false, estimate_above_cap: false,
})
const lines = [line(1, false), line(2, true), line(3, false)]
const preview: GlossaryAffectedPreview = {
  drama_id: 7, has_glossary: true, terms: [], selected_term_ids: [1], lines, hand_edited_count: 1,
  preview_hash: 'abc', estimate: est(0.2, 2), estimate_with_hand_edited: est(0.3, 3),
}
const form: RunForm = {
  engine: 'claude', model: '', style_preset: 'audio_drama', style_note: '', locale: 'en-GB',
  batch_size: '20', context_window: '6', context_window_ahead: '3', cost_cap: '1.5', fallbacks: [],
  force: true, forceConfirmed: true, reflect: false, bulk: true, female_pronouns: false, genre_notes: true,
}

describe('glossary re-translate selection', () => {
  it('ticks only machine lines by default', () => {
    expect([...defaultSelection(lines)]).toEqual([1, 3])
  })

  it('never sends a hand-edited line unless included', () => {
    const all = new Set([1, 2, 3])
    expect(chosenIds(lines, all, false)).toEqual([1, 3])
    expect(chosenIds(lines, all, true)).toEqual([1, 2, 3])
    expect(chosenIds(lines, new Set([3]), true)).toEqual([3])
  })

  it('shows an exact estimate only for the default or full selection', () => {
    expect(selectionEstimate(preview, [1, 3], false)).toEqual({ estimate: preview.estimate, exact: true })
    expect(selectionEstimate(preview, [1], false).exact).toBe(false)
    expect(selectionEstimate(preview, [1, 2, 3], true)).toEqual({ estimate: preview.estimate_with_hand_edited, exact: true })
    expect(costText(est(0.2, 2), true)).toBe('about $0.20')
    expect(costText(est(0.2, 2), false)).toBe('at most $0.20')
    expect(costText({ ...est(null, 0), free: true }, true)).toBe('free')
  })
})

describe('glossary re-translate requests', () => {
  it('uses the Translate form for the preview and the run, never force or bulk', () => {
    expect(affectedParams(form, [4, 5])).toEqual({ term_ids: [4, 5], engine: 'claude', job_cost_cap_usd: 1.5 })
    expect(affectedParams({ ...form, cost_cap: '-1' }, [])).toBeNull()
    const body = buildAffectedRunBody(form, { ...preview, selected_term_ids: [] }, [1, 3], false)
    expect(body).toMatchObject({ line_ids: [1, 3], preview_hash: 'abc', include_hand_edited: false, locale: 'en-GB', engine: 'claude', job_cost_cap_usd: 1.5 })
    expect(body).not.toHaveProperty('force_retranslate')
    expect(body).not.toHaveProperty('bulk')
    expect(body).not.toHaveProperty('term_ids')
    // The preview's terms, not whatever is ticked now.
    expect(buildAffectedRunBody(form, { ...preview, selected_term_ids: [9] }, [2], true).term_ids).toEqual([9])
  })

  it('builds the preview query and posts the run', async () => {
    expect(buildGlossaryAffectedQuery({ term_ids: [1, 2], engine: 'claude' })).toBe('?term_ids=1&term_ids=2&engine=claude')
    expect(buildGlossaryAffectedQuery({})).toBe('')
    const calls: { url: string; init?: RequestInit }[] = []
    const fake = (async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), init })
      return new Response(JSON.stringify(preview), { status: 200 })
    }) as typeof fetch
    await getGlossaryAffected(7, { reflect: true }, fake)
    await startGlossaryAffectedRun(7, buildAffectedRunBody(form, preview, [1], false), fake)
    expect(calls[0].url).toContain('/api/translate-run/dramas/7/glossary-affected?reflect=true')
    expect(calls[1].url).toContain('/api/translate-run/dramas/7/glossary-affected/run')
    expect(calls[1].init?.method).toBe('POST')
  })
})
