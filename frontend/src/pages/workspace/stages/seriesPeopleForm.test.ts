import { describe, expect, it } from 'vitest'

import { addSeriesPerson, updateSeriesPerson } from '../../../api/seriesPeople'
import type { SeriesCharacter } from '../../../types/autotuneGlossary'
import {
  CUSTOM,
  buildPersonCreate,
  buildPersonUpdate,
  formPronouns,
  isPersonDirty,
  personProblem,
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
