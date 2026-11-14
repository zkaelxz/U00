import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { CharacterEntry } from '../../types/translateStage'
import { chooseEngine } from '../../types/listen'
import { ListenPicker } from './ListenSection'

const voice = (extra: Partial<CharacterEntry>): CharacterEntry =>
  ({ speaker_label: 'Narrator', character_name: 'Narrator', pronouns: '', clone_engine: '', voice_design: '',
    has_ref_audio: false, ref_text_present: false, series_character_id: null, series_character_name: '',
    line_count: 3, ...extra })

const engines = [
  { key: 'omnivoice', label: 'OmniVoice', unavailable_reason: 'OmniVoice is not installed. Install it in Diagnostics.' },
  { key: 'qwen3_tts', label: 'Qwen3-TTS', unavailable_reason: null },
]

describe('ListenPicker', () => {
  it('lists engines that cannot run as disabled options with the reason', () => {
    const out = renderToStaticMarkup(
      <ListenPicker dramaId={22} engines={engines} engine="qwen3_tts" voices={[]} disabled={false} onChange={() => undefined} />,
    )
    expect(out).toContain('Voice engine')
    expect(out).toMatch(/<option value="omnivoice" disabled="">OmniVoice \(OmniVoice is not installed/)
    expect(out).toMatch(/<option value="qwen3_tts"( selected="")?>Qwen3-TTS<\/option>/)
  })

  it('says how each speaker\'s voice is set up and links to where to change it', () => {
    const out = renderToStaticMarkup(
      <ListenPicker dramaId={22} engines={engines} engine="qwen3_tts" disabled={false} onChange={() => undefined}
        voices={[voice({ has_ref_audio: true }), voice({ speaker_label: 'B', character_name: '', voice_design: 'low voice' }), voice({ speaker_label: 'C' })]} />,
    )
    expect(out).toContain('Narrator: reference clip')
    expect(out).toContain('B: described voice')
    expect(out).toContain('Narrator: no voice yet')
    expect(out).toContain('href="#/drama/22/review?tools=change"')
    expect(out).toContain('Change voice')
  })
})

describe('voice language', () => {
  it('offers translation or the original language and shows the current one', () => {
    const out = renderToStaticMarkup(
      <ListenPicker dramaId={22} engines={engines} engine="qwen3_tts" voices={[]} disabled={false} onChange={() => undefined}
        languages={['translation', 'original']} language="original" onLanguage={() => undefined} />,
    )
    expect(out).toContain('Voice language')
    expect(out).toMatch(/<option value="translation">Translation<\/option>/)
    expect(out).toMatch(/<option value="original"( selected="")?>Original language<\/option>/)
  })

  it('shows no language choice when the server offers one', () => {
    const out = renderToStaticMarkup(
      <ListenPicker dramaId={22} engines={engines} engine="qwen3_tts" voices={[]} disabled={false} onChange={() => undefined}
        languages={['translation']} language="translation" onLanguage={() => undefined} />,
    )
    expect(out).not.toContain('Voice language')
  })
})

describe('chooseEngine', () => {
  it('starts on an engine that can run when the default cannot', () => {
    expect(chooseEngine(engines, 'omnivoice', '')).toBe('qwen3_tts')
  })

  it('keeps the default while it can run and a pick while it can run', () => {
    const ready = engines.map((e) => ({ ...e, unavailable_reason: null }))
    expect(chooseEngine(ready, 'omnivoice', '')).toBe('omnivoice')
    expect(chooseEngine(ready, 'omnivoice', 'qwen3_tts')).toBe('qwen3_tts')
    expect(chooseEngine(engines, 'omnivoice', 'omnivoice')).toBe('qwen3_tts')
  })

  it('still names the default when nothing can run, so the blocker explains why', () => {
    const none = engines.map((e) => ({ ...e, unavailable_reason: 'not installed' }))
    expect(chooseEngine(none, 'omnivoice', '')).toBe('omnivoice')
    expect(chooseEngine([], 'omnivoice', '')).toBe('')
  })
})
