import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { SettingsSchemaRow } from '../../types/settings'
import { TimingSettingsView, type TimingViewProps } from './TimingSettingsCard'
import { timingGroups } from './timingSettings'

const row = (over: Partial<SettingsSchemaRow>): SettingsSchemaRow => ({
  key: 'split_max_seconds', type: 'float', default: 8, scope: 'app', label: 'Longest line (time)',
  help: 'Lines that run longer than this are cut at sentence ends, commas or pauses.', unit: 's', placeholder: '',
  tab: 'system', section: 'Line splitting', custom: false, dev_only: false, choices: null, min: 4, max: 20,
  max_len: null, multiline: false, ...over,
})

const SCHEMA = [
  row({ key: 'qwen_vad_threshold', section: 'Voice detection', label: 'Qwen3 detection: confidence', help: 'How sure the detector must be.', dev_only: true, default: 0.5, min: 0.2, max: 0.8, unit: '' }),
  row({}),
  row({ key: 'scene_break_gap_seconds', section: 'Sentence grouping', label: 'Pause that counts as a scene change', help: 'Translation batches end at a pause this long.', default: 3, min: 1.5, max: 15 }),
]

const noop = () => {}
function view(over: Partial<TimingViewProps> = {}) {
  const developerMode = over.developerMode ?? true
  const props: TimingViewProps = {
    groups: timingGroups(SCHEMA, developerMode), developerMode,
    saved: { qwen_vad_threshold: 0.5, split_max_seconds: 8, scene_break_gap_seconds: 3 },
    draft: {}, errors: {}, busy: false, note: null,
    onChange: noop, onReset: noop, onSave: noop, onUndo: noop, ...over,
  }
  return renderToStaticMarkup(<TimingSettingsView {...props} />)
}

describe('TimingSettingsView', () => {
  it('shows the groups in order, each with its rows', () => {
    const html = view()
    const at = (s: string) => html.indexOf(`>${s}</h2>`)
    expect(at('Voice detection')).toBeGreaterThan(-1)
    expect(at('Voice detection')).toBeLessThan(at('Line splitting'))
    expect(at('Line splitting')).toBeLessThan(at('Sentence grouping'))
    expect(html).toContain('Longest line (time)')
    expect(html).toContain('Pause that counts as a scene change')
  })

  it('shows each row\'s current value, its default and its plain tooltip', () => {
    const html = view({ saved: { qwen_vad_threshold: 0.5, split_max_seconds: 10, scene_break_gap_seconds: 3 } })
    expect(html).toMatch(/value="10"/)
    expect(html).toContain('Default: 8 s')
    expect(html).toContain('Default: 0.5')
    expect(html).toContain('role="tooltip"')
    expect(html).toContain('Lines that run longer than this are cut at sentence ends, commas or pauses.')
    expect(html).toContain('aria-label="Help: Longest line (time)"')
  })

  it('offers Reset on every row, enabled only when the value is not the default', () => {
    const html = view({ saved: { qwen_vad_threshold: 0.5, split_max_seconds: 10, scene_break_gap_seconds: 3 } })
    const button = (label: string) => html.match(new RegExp(`<button[^>]*aria-label="Reset ${label} to default"[^>]*>`))?.[0] ?? ''
    expect(button('Longest line \\(time\\)')).not.toBe('')
    expect(button('Longest line \\(time\\)')).not.toContain('disabled')
    expect(button('Pause that counts as a scene change')).toContain('disabled')
    expect(button('Qwen3 detection: confidence')).toContain('disabled')
  })

  it('keeps Developer Mode rows out, and says so, while Developer Mode is off', () => {
    const html = view({ developerMode: false })
    expect(html).not.toContain('Qwen3 detection: confidence')
    expect(html).not.toContain('>Voice detection</h2>')
    expect(html).toContain('More settings appear here when Developer Mode is on.')
    expect(view()).not.toContain('More settings appear here')
  })

  it('says a change only affects future runs', () => {
    expect(view()).toContain('A changed value only affects runs you start afterwards.')
  })

  it('enables Save and offers Undo only after an edit, and shows a row\'s error', () => {
    const clean = view()
    expect(clean).toMatch(/<button[^>]*disabled=""[^>]*>Save<\/button>/)
    expect(clean).not.toContain('Undo changes')
    const edited = view({ draft: { split_max_seconds: '3' }, errors: { split_max_seconds: 'Enter an amount from 4 to 20.' } })
    expect(edited).not.toMatch(/<button[^>]*disabled=""[^>]*>Save<\/button>/)
    expect(edited).toContain('Undo changes')
    expect(edited).toContain('Enter an amount from 4 to 20.')
  })
})
