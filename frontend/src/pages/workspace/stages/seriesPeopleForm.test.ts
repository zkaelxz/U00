import { describe, expect, it } from 'vitest'

import { addSeriesPerson, updateSeriesPerson } from '../../../api/seriesPeople'
import type { SeriesCharacter } from '../../../types/autotuneGlossary'
import {
  CLEAR_PRONOUNS,
  CUSTOM,
  buildPersonCreate,
  buildPersonUpdate,
  bulkPronounsProblem,
  bulkPronounsSummary,
  formPronouns,
  isPersonDirty,
  peopleCount,
  personProblem,
  planBulkPronouns,
  toBulkPronounsForm,
  toPersonForm,
} from './seriesPeopleForm'

const mei: SeriesCharacter = { id: 11, character_name: 'Mei', aliases: '梅', notes: 'calm', pronouns: 'she/her' }

describe('series person form', () => {
  it('sends nothing when unchanged', () => {
    const u = buildPersonUpdate(mei, toPersonForm(mei))
    expect(u).toEqual({})
    expect(isPersonDirty(u)).toBe(false)
  })

  it('sends only changed fields, trimmed, "" to clear', () => {
    const f = { ...toPersonForm(mei), character_name: ' Meiling ', pronoun_choice: 'they/them', aliases: '' }
    expect(buildPersonUpdate(mei, f)).toEqual({ character_name: 'Meiling', pronouns: 'they/them', aliases: '' })
    expect(buildPersonUpdate(mei, { ...toPersonForm(mei), pronoun_choice: '' })).toEqual({ pronouns: '' })
  })

  it('maps legacy and custom pronouns', () => {
    const legacy = { ...mei, pronouns: 'female' }
    expect(toPersonForm(legacy).pronoun_choice).toBe('she/her')
    expect(buildPersonUpdate(legacy, toPersonForm(legacy))).toEqual({})
    const xe = { ...mei, pronouns: 'xe/xem' }
    const f = toPersonForm(xe)
    expect(f.pronoun_choice).toBe(CUSTOM)
    expect(f.custom_pronouns).toBe('xe/xem')
    // Custom with nothing typed keeps the saved value.
    expect(formPronouns({ ...f, custom_pronouns: ' ' }, 'xe/xem')).toBe('xe/xem')
    expect(buildPersonUpdate(xe, { ...f, custom_pronouns: 'ze/zir' })).toEqual({ pronouns: 'ze/zir' })
  })

  it('refuses a blank name and builds a create body', () => {
    expect(personProblem({ ...toPersonForm(), character_name: '  ' })).toBe('A name cannot be blank.')
    const f = { ...toPersonForm(), character_name: ' Lan ', pronoun_choice: CUSTOM, custom_pronouns: ' xe/xem ', aliases: ' 兰 ' }
    expect(personProblem(f)).toBeNull()
    expect(buildPersonCreate(f)).toEqual({ character_name: 'Lan', pronouns: 'xe/xem', aliases: '兰', notes: '' })
  })
})

describe('bulk pronouns', () => {
  const wei: SeriesCharacter = { id: 12, character_name: 'Wei', aliases: '', notes: '', pronouns: 'male' }
  const lan: SeriesCharacter = { id: 13, character_name: 'Lan', aliases: '', notes: '', pronouns: '' }
  const cast = [mei, wei, lan]

  it('names the fix while disabled', () => {
    expect(bulkPronounsProblem(0, { choice: 'he/him', custom: '' })).toBe('Still needed: tick at least one person in the list.')
    expect(bulkPronounsProblem(2, { choice: CUSTOM, custom: '  ' })).toBe('Still needed: type the custom pronouns.')
    expect(bulkPronounsProblem(2, { choice: CUSTOM, custom: 'xe/xem' })).toBeNull()
    // Nothing is chosen at first, so one tap can't clear everyone; Unspecified must be picked.
    expect(bulkPronounsProblem(1, toBulkPronounsForm())).toBe('Still needed: choose the pronouns to set.')
    expect(bulkPronounsProblem(1, { choice: CLEAR_PRONOUNS, custom: '' })).toBeNull()
  })

  it('sends only {pronouns}, picked by id, skipping people who already match', () => {
    const plan = planBulkPronouns(cast, new Set([13, 12, 99]), { choice: 'he/him', custom: '' })
    expect(plan.body).toEqual({ pronouns: 'he/him' })
    // Legacy "male" already means he/him.
    expect(plan.send.map((p) => p.id)).toEqual([13])
    expect(plan.unchanged.map((p) => p.id)).toEqual([12])
  })

  it('clears with "" and trims a custom value', () => {
    expect(planBulkPronouns(cast, new Set([11, 13]), { choice: CLEAR_PRONOUNS, custom: '' })).toMatchObject({
      body: { pronouns: '' },
      send: [mei],
      unchanged: [lan],
    })
    expect(planBulkPronouns(cast, new Set([11]), { choice: CUSTOM, custom: ' xe/xem ' }).body).toEqual({ pronouns: 'xe/xem' })
  })

  it('summarises a run in plain words', () => {
    expect(peopleCount(1)).toBe('1 person')
    expect(peopleCount(3)).toBe('3 people')
    expect(bulkPronounsSummary({ updated: 2, unchanged: 0, failed: 0 }, 'he/him')).toBe('Updated 2 people.')
    expect(bulkPronounsSummary({ updated: 1, unchanged: 1, failed: 1 }, 'she/her')).toBe(
      'Updated 1 person. 1 person already had she/her. 1 person could not be updated.',
    )
    expect(bulkPronounsSummary({ updated: 0, unchanged: 2, failed: 0 }, '')).toBe('2 people already had no pronouns set.')
  })
})

describe('series people api', () => {
  it('posts JSON to the add and edit routes, addressed by id', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    const f = (async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), init })
      return new Response(JSON.stringify(mei), { status: 200 })
    }) as typeof fetch
    await addSeriesPerson(5, { character_name: 'Mei' }, f)
    await updateSeriesPerson(5, 11, { pronouns: 'he/him' }, f)
    expect(calls.map((c) => c.url)).toEqual(['/api/characters/series/5/characters', '/api/characters/series/5/characters/11'])
    expect(calls.map((c) => c.init?.method)).toEqual(['POST', 'POST'])
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({ pronouns: 'he/him' })
    expect(new Headers(calls[0].init?.headers).get('Content-Type')).toBe('application/json')
  })

  it('surfaces a 409 as an ApiError with its code', async () => {
    const f = (async () =>
      new Response(JSON.stringify({ error: { code: 'conflict', message: 'taken' } }), { status: 409 })) as typeof fetch
    await expect(addSeriesPerson(5, { character_name: 'Mei' }, f)).rejects.toMatchObject({ status: 409, code: 'conflict' })
  })
})
