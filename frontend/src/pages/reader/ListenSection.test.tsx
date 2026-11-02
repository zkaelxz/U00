import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { CharacterEntry } from '../../types/translateStage'
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
    expect(out).toContain('href="#/drama/22/review?tools=voices"')
    expect(out).toContain('Change voice')
  })
})
