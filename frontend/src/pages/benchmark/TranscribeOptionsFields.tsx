import { useState } from 'react'

import type { BenchmarkConfig, BenchmarkOptions, BenchmarkTranscribeOptions } from '../../api/benchmark'
import { Field } from '../../components/Field'
import { Toggle } from '../../components/Toggle'
import { buttonClass } from '../../components/uiClasses'
import { rangeHint } from '../workspace/formatRange'
import { RANGES } from '../workspace/sourceForm'
import { optionValue, recogniserLabel, withOption } from './benchmarkForm'

type NumberKey = 'beam_size' | 'min_silence_ms' | 'vad_threshold' | 'hallucination_silence_sec' | TimingKey
type TimingKey = 'speech_pad_ms' | 'split_max_seconds' | 'split_max_chars'
type ToggleKey = 'whisper_repeat_guard' | 'difficult_audio' | 'separate_vocals_first' | 'refine_timing'

const HALLUCINATION_RANGE = { min: 0.5, max: 10 }
/** Lab-only timing settings; the server checks the same ranges. */
const TIMING_RANGES: Record<TimingKey, { min: number; max: number; integer: boolean }> = {
  speech_pad_ms: { min: 0, max: 2000, integer: true },
  split_max_seconds: { min: 2, max: 30, integer: false },
  split_max_chars: { min: 10, max: 120, integer: true },
}

/** The error for a typed number, or null when it is acceptable (the server checks again). */
function numberProblem(key: NumberKey, value: number): string | null {
  if (key === 'hallucination_silence_sec') {
    return value === 0 || (value >= HALLUCINATION_RANGE.min && value <= HALLUCINATION_RANGE.max) ? null : 'Use 0 (off) or 0.5 to 10.'
  }
  const r = key in TIMING_RANGES ? TIMING_RANGES[key as TimingKey] : RANGES[key as keyof typeof RANGES]
  if (value < r.min || value > r.max) return `Use ${r.min} to ${r.max}.`
  return 'integer' in r && r.integer && !Number.isInteger(value) ? 'Use a whole number.' : null
}

interface Props {
  options: BenchmarkOptions
  config: BenchmarkConfig
  onChange: (next: BenchmarkConfig) => void
}

/** The per-title Transcribe options for one Lab arm, folded away and set to the app's defaults. */
export function TranscribeOptionsFields({ options, config, onChange }: Props) {
  const defaults = options.transcribe_defaults
  const ignored = options.transcribe_options_ignored[config.engine] ?? []
  const changed = Object.keys(config.options ?? {}).length
  // What is typed in a number box until it is a valid number, so clearing the box to retype does not snap back to the default.
  const [draft, setDraft] = useState<Partial<Record<NumberKey, string>>>({})
  const set = (key: keyof BenchmarkTranscribeOptions, value: unknown) => onChange(withOption(config, defaults, key, value))
  const unused = (key: keyof BenchmarkTranscribeOptions) => (ignored.includes(key) ? `Not used by ${recogniserLabel(options, config)}.` : null)

  const number = (key: NumberKey, label: string, step: number, help: string, unit?: string) => {
    const reason = unused(key)
    const typed = draft[key]
    const shown = typed ?? String(optionValue(config, defaults, key))
    const parsed = typed === undefined ? null : typed.trim() === '' ? null : Number(typed)
    const problem = parsed !== null && Number.isFinite(parsed) ? numberProblem(key, parsed) : null
    return (
      <Field label={label} unit={unit} help={reason ? `${reason} ${help}` : help} error={problem}>
        <input
          type="number"
          step={step}
          value={shown}
          disabled={!!reason}
          onChange={(e) => {
            const text = e.target.value
            setDraft({ ...draft, [key]: text })
            if (text.trim() === '') return set(key, undefined)
            const n = Number(text)
            if (Number.isFinite(n) && !numberProblem(key, n)) set(key, n)
          }}
          onBlur={() => setDraft({ ...draft, [key]: undefined })}
        />
      </Field>
    )
  }
  const toggle = (key: ToggleKey, label: string, help: string) => {
    const reason = unused(key)
    return (
      <Field label={label} help={reason ? `${reason} ${help}` : help}>
        <Toggle checked={optionValue(config, defaults, key)} disabled={!!reason} onChange={(v) => set(key, v)} />
      </Field>
    )
  }
  const promptReason = unused('initial_prompt')

  return (
    <details className="bench-real-settings" data-testid="bench-real-settings">
      <summary>
        Settings like a real run
        {changed > 0 && <span className="muted"> · {changed} changed</span>}
      </summary>
      <p className="muted">
        The same options as a title&rsquo;s Transcribe stage, starting at what a title uses when you have not changed anything.
        A run records the ones it used.
      </p>
      <div className="bench-config-fields">
        {number('beam_size', 'Beam size', 1, `${rangeHint(RANGES.beam_size.min, RANGES.beam_size.max, { default: defaults.beam_size })} Higher searches more alternatives, slower.`)}
        {number('min_silence_ms', 'Min silence', 50, `${rangeHint(RANGES.min_silence_ms.min, RANGES.min_silence_ms.max, { unit: 'ms', default: defaults.min_silence_ms })} Silence that splits lines.`, 'ms')}
        {number('vad_threshold', 'VAD threshold', 0.05, `${rangeHint(RANGES.vad_threshold.min, RANGES.vad_threshold.max, { default: defaults.vad_threshold })} Higher ignores more quiet sound.`)}
        <Field
          label="Sensitivity"
          help={[unused('sensitivity_preset'), 'Catches quieter or faster speech, but may add false text on music or breathing.'].filter(Boolean).join(' ')}
        >
          <select
            value={optionValue(config, defaults, 'sensitivity_preset')}
            disabled={!!unused('sensitivity_preset')}
            onChange={(e) => set('sensitivity_preset', e.target.value)}
          >
            <option value="normal">Normal</option>
            <option value="sensitive">Sensitive</option>
          </select>
        </Field>
        {number('hallucination_silence_sec', 'Hallucination skip', 0.5, `0 is off, otherwise 0.5 to 10 s, default ${defaults.hallucination_silence_sec} s. Skips text over a long silent gap.`, 's')}
      </div>
      <div className="setting-list">
        {toggle('whisper_repeat_guard', 'Repeat guard', 'Penalises a phrase the model keeps repeating.')}
        {toggle('difficult_audio', 'Difficult audio', 'For screaming, whispering or talk over each other; slower and may add a few false lines.')}
        {toggle('separate_vocals_first', 'Separate vocals first', 'Runs vocal separation before transcribing; can drop quiet speech.')}
      </div>
      <h4 className="bench-timing-heading">Line timing</h4>
      <p className="muted">Where lines start and end. Scored on test-set clips that have reference times.</p>
      <div className="bench-config-fields">
        {number('speech_pad_ms', 'Speech pad', 50, `${rangeHint(0, 2000, { unit: 'ms', default: defaults.speech_pad_ms })} Audio kept around each stretch of speech.`, 'ms')}
        {number('split_max_seconds', 'Longest line', 1, `${rangeHint(2, 30, { unit: 's', default: defaults.split_max_seconds })} Longer lines are split.`, 's')}
        {number('split_max_chars', 'Longest line, characters', 5, `${rangeHint(10, 120, { default: defaults.split_max_chars })} Longer lines are split.`)}
      </div>
      <div className="setting-list">
        {toggle('refine_timing', 'Refine line timing', 'Re-times each line with the Qwen3 forced aligner. Only the speech-detection Qwen3-ASR recogniser uses it.')}
      </div>
      <Field label="Initial prompt" help={[promptReason, 'Names and terms to prime Whisper with. A title builds this from its glossary; here you type it.'].filter(Boolean).join(' ')}>
        <input
          value={optionValue(config, defaults, 'initial_prompt')}
          maxLength={1000}
          disabled={!!promptReason}
          placeholder="Optional"
          onChange={(e) => set('initial_prompt', e.target.value)}
        />
      </Field>
      {changed > 0 && (
        <div className="actions">
          <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => { setDraft({}); const { options: _o, ...rest } = config; onChange(rest) }}>
            Back to the app&rsquo;s defaults
          </button>
        </div>
      )}
    </details>
  )
}
