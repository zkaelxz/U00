import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { LibraryPreset } from '../../types/library'
import { ProfileEditor, type ProfileEditorData } from './ProfileEditor'
import { emptyProfile, fromPreset } from './profileForm'

const data: ProfileEditorData = {
  engines: [
    { name: 'claude', label: 'Claude', free: false, models: ['claude-a'], key_configured: true },
    { name: 'deepseek', label: 'DeepSeek', free: false, models: null, key_configured: true },
  ],
  style_presets: [{ key: 'audio_drama', label: 'Audio drama' }],
  fields: [
    { key: 'whisper_size', label: 'Whisper model', group: 'transcribe', type: 'choice', choices: ['small'] },
    { key: 'beam_size', label: 'Beam size', group: 'transcribe', type: 'int', min: 1, max: 10 },
  ],
}
const noop = () => undefined

describe('ProfileEditor', () => {
  it('offers translation and transcription options, never a language, English variant or glossary', () => {
    const html = renderToStaticMarkup(
      <ProfileEditor data={data} initial={emptyProfile('claude', 'audio_drama')} existing={null} onSaved={noop} onCancel={noop} />,
    )
    expect(html).toContain('aria-label="New profile"')
    for (const label of ['Engine', 'Model', 'Style', 'Reflect', 'Cost cap per job', 'Whisper model', 'Beam size']) {
      expect(html).toContain(label)
    }
    expect(html).not.toMatch(/Source language|English variant<|Glossary<|Locale/i)
    expect(html).toContain('Save profile')
  })

  it('edits an existing profile in place', () => {
    const p = { id: 4, name: 'Mine', translation_engine: 'claude', engine_model: null, style_preset: null, locale: null } as LibraryPreset
    const html = renderToStaticMarkup(
      <ProfileEditor data={data} initial={fromPreset(p, 'audio_drama')} existing={p} onSaved={noop} onCancel={noop} />,
    )
    expect(html).toContain('aria-label="Edit profile Mine"')
    expect(html).toContain('Save changes')
    expect(html).toContain('value="Mine"')
  })
})
