import { describe, expect, it } from 'vitest'

import { answerVoiceSuggestion, getVoiceSuggestions, rememberSeriesCharacter, renameSpeaker, undoRenameSpeaker } from './characters'

type Call = { url: string; init?: RequestInit }

function fakeFetch(calls: Call[], body = '{}') {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init })
    return new Response(body, { status: 200 })
  }) as typeof fetch
}

describe('characters extras API', () => {
  it('lists voice suggestions with a GET', async () => {
    const calls: Call[] = []
    const out = await getVoiceSuggestions(3, fakeFetch(calls, '[]'))
    expect(out).toEqual([])
    expect(calls[0].url).toBe('/api/characters/dramas/3/voice-suggestions')
    expect(calls[0].init?.method ?? 'GET').toBe('GET')
  })

  it.each(['accept', 'reject'] as const)('posts %s with only the label and id', async (action) => {
    const calls: Call[] = []
    await answerVoiceSuggestion(
      3,
      action,
      { speaker_label: 'SPEAKER 00/x', series_character_id: 7, character_name: 'Lin', similarity: 0.9 } as never,
      fakeFetch(calls),
    )
    expect(calls[0].url).toBe(`/api/characters/dramas/3/voice-suggestions/${action}`)
    expect(calls[0].init?.method).toBe('POST')
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ speaker_label: 'SPEAKER 00/x', series_character_id: 7 })
  })

  it('posts remember with the speaker label in the body', async () => {
    const calls: Call[] = []
    await rememberSeriesCharacter(5, 'A', fakeFetch(calls))
    expect(calls[0].url).toBe('/api/characters/dramas/5/remember-series-character')
    expect(calls[0].init?.method).toBe('POST')
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ speaker_label: 'A' })
  })
})

describe('rename speaker API', () => {
  it('posts only the label and the new name', async () => {
    const calls: Call[] = []
    await renameSpeaker(3, 'Speaker 1', 'Mei', fakeFetch(calls))
    expect(calls[0].url).toBe('/api/characters/dramas/3/rename-speaker')
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ speaker_label: 'Speaker 1', new_name: 'Mei' })
  })

  it('posts the undo it was given', async () => {
    const calls: Call[] = []
    const undo = { speaker_label: 'Mei', previous_label: 'Speaker 1', previous_character_name: null, previous: [] }
    await undoRenameSpeaker(3, undo, fakeFetch(calls))
    expect(calls[0].url).toBe('/api/characters/dramas/3/rename-speaker/undo')
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ undo })
  })
})
