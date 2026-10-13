import { Fragment, type ReactNode } from 'react'

import { asrBackendOptionsFor } from '../../../api/asrOptions'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { Section } from '../../../components/Section'
import { Toggle } from '../../../components/Toggle'
import { buttonClass } from '../../../components/uiClasses'
import type { TranscribeConfig } from '../../../types/workspace'
import type { TitleKind } from '../titleKind'
import { rangeHint } from '../formatRange'
import {
  advancedSummary,
  BEAM_SIZE_DEFAULT,
  DEFAULT_HALLUCINATION_SILENCE_SEC,
  HARDSUB_INTERVAL_DEFAULT,
  MIN_PAUSE_SEC_DEFAULT,
  MIN_SILENCE_MS_DEFAULT,
  RANGES,
  VAD_THRESHOLD_DEFAULT,
  type RunField,
} from '../sourceForm'
import { advancedGroups, type AdvancedFieldId } from './advancedGroups'
import { DiarizationDeviceNote } from './DiarizationDeviceNote'
import { SpeakerDetectionNotice } from './SpeakerDetectionNotice'
import { optionLabel, promptSummary, readableSummary, speakersSummary, switchesSummary, type ConfigForm } from './transcribeConfigForm'
import { diarizeEstimate } from './transcribeEstimate'
import { asrBackendHelp, GROQ_HELP } from './transcribeBackendField'
import { whisperOnlyControlReason, type WhisperOnlyControl } from './transcribeRun'
import { VoiceDetectorField } from './VoiceDetectorField'
import { NeedsInstall } from '../../readiness/NeedsInstall'
import { QWEN3_BACKENDS, unavailableBackends, type Need, type TranscribeNeeds } from '../../readiness/needs'

type NumKey = 'beam_size' | 'min_silence_ms' | 'min_pause_sec' | 'vad_threshold' | 'hallucination_silence_sec' | 'hardsub_interval_sec'
type SelectKey = 'alignment_method' | 'asr_backend_choice' | 'separation_backend' | 'hardsub_ocr_backend' | 'sensitivity_preset'
type ToggleKey = 'separate_vocals_first' | 'realign_long_segments' | 'whisper_fast_mode' | 'whisper_repeat_guard' | 'split_by_sentences' | 'vocabulary_hint' | 'use_groq'

export interface SpeakerInputs {
  expected: string
  min: string
  max: string
  set: (field: 'expected' | 'min' | 'max', value: string) => void
}

interface Props {
  dramaId: number
  cf: ConfigForm
  config: TranscribeConfig | null
  setC: <K extends keyof ConfigForm>(k: K, v: ConfigForm[K]) => void
  language: string
  // Controls the kind has no use for (mediaTypeSettings.ts) are not rendered.
  kind: TitleKind
  developerMode: boolean
  haveTranscript: boolean
  // What the controls need from this PC (null while readiness is unknown: nothing is switched off).
  needs: TranscribeNeeds | null
  onNeedInstalled: () => void
  // The title's options were saved before (see optionsSaved in TranscribeStage): decides which fold opens first.
  optionsSaved: boolean
  fieldError: (f: RunField) => string | null
  speakers: SpeakerInputs
  extraNames: string
  setExtraNames: (v: string) => void
  override: string
  setOverride: (v: string) => void
  // Hand-corrected speakers: detection keeps them unless replacing is confirmed.
  manualCount: number
  corrections: string
  overwriteManual: boolean
  setOverwriteManual: (v: boolean) => void
  overwriteAck: boolean
  setOverwriteAck: (v: boolean) => void
  needsAck: boolean
  onDiarize: () => void
  busy: boolean
  hasMedia: boolean
  duration: number | null
  onSave: () => void
  onReset: () => void
  saved: boolean
  // The refused-option sentence and the run/save error, shown beside Save options.
  problem: string | null
  error: unknown
  onDismissError: () => void
}

type Placed = { zone: 'grid' | 'block' | 'list'; node: ReactNode }

// The advanced options, folded by purpose (advancedGroups.ts decides where each control sits).
export function TranscribeAdvanced(p: Props) {
  const { cf, config, setC, language, developerMode, haveTranscript } = p

  // The way to fix a missing need, under the control it switches off.
  const fix = (need: Need | null | undefined) => (need ? <NeedsInstall need={need} onInstalled={p.onNeedInstalled} /> : null)
  const select = (label: string, key: SelectKey, options: string[], help?: string, disabled: string[] = [], below: ReactNode = null): Placed => ({
    zone: 'grid',
    node: (
      <div>
        <Field label={label} help={help} error={key === 'alignment_method' || key === 'asr_backend_choice' ? p.fieldError(key) : null}>
          <select value={cf[key]} onChange={(e) => setC(key, e.target.value)}>
            {(options.includes(cf[key]) ? options : [cf[key], ...options]).map((o) => (
              <option key={o} value={o} disabled={disabled.includes(o) && o !== cf[key]}>{optionLabel(o)}</option>
            ))}
          </select>
        </Field>
        {below}
      </div>
    ),
  })
  // A pasted transcript is aligned, so the ASR backend choice does not apply to that run.
  const unusedReason = (control?: WhisperOnlyControl) => (control && !haveTranscript ? whisperOnlyControlReason(cf.asr_backend_choice, control) : null)
  const withReason = (help: string | undefined, reason: string | null) => (reason ? [reason + '.', help].filter(Boolean).join(' ') : help)
  const num = (label: string, key: NumKey, step: number, help: string, unit?: string, control?: WhisperOnlyControl): Placed => {
    const reason = unusedReason(control)
    return {
      zone: 'grid',
      node: (
        <Field label={label} help={withReason(help, reason)} unit={unit}>
          <input type="number" step={step} value={cf[key]} disabled={!!reason} onChange={(e) => setC(key, e.target.value)} />
        </Field>
      ),
    }
  }
  // `missing`: what this switch needs from the PC. Off, it cannot be switched on and the fix is shown under
  // it; already on (a kept draft), it can still be turned off and the Before-you-run list carries the fix.
  const toggle = (label: string, key: ToggleKey, help: string, control?: WhisperOnlyControl, missing?: Need | null): Placed => {
    const reason = unusedReason(control)
    const unavailable = !!missing && !cf[key]
    return {
      zone: 'list',
      node: (
        <>
          <Field label={label} help={withReason(help, reason)}>
            <Toggle checked={cf[key]} disabled={!!reason || unavailable} title={reason ?? (unavailable ? missing.reason : undefined)} onChange={(v) => setC(key, v)} />
          </Field>
          {unavailable && fix(missing)}
        </>
      ),
    }
  }
  const speaker = (label: string, field: 'expected' | 'min' | 'max', help: string, range?: boolean): Placed => ({
    zone: 'grid',
    node: (
      <Field label={label} help={help} error={field === 'expected' ? p.fieldError('speakers') : null}>
        <input
          type="number"
          {...(range ? { min: 1, max: 20 } : {})}
          value={p.speakers[field]}
          onChange={(e) => p.speakers.set(field, e.target.value)}
        />
      </Field>
    ),
  })

  const speakersMissing = !!p.needs && p.needs.speakers.length > 0
  const asrOptions = asrBackendOptionsFor(language)
  const fields: Record<AdvancedFieldId, () => Placed> = {
    expected_speakers: () => speaker('Expected speakers', 'expected', `${rangeHint(0, 20)} Blank lets the app decide.`),
    min_speakers: () =>
      speaker('Min speakers', 'min', `${rangeHint(1, 20)} When you know a range but not the exact count. Used by Detect speakers only and by detecting speakers after transcribing.`, true),
    max_speakers: () => speaker('Max speakers', 'max', `${rangeHint(1, 20)} Leave Expected speakers blank when using a range.`, true),
    detect_speakers_only: () => ({
      zone: 'block',
      node: (
        <>
          {p.manualCount > 0 && (
            <div className="source-manual" data-testid="manual-speakers">
              <div className="setting-list">
                <Field
                  label={`Replace my ${p.corrections}`}
                  help="Off keeps your corrections: detection only changes the lines you haven't corrected. On replaces them with what detection finds."
                >
                  <Toggle
                    checked={p.overwriteManual}
                    onChange={(v) => {
                      p.setOverwriteManual(v)
                      p.setOverwriteAck(false)
                    }}
                  />
                </Field>
              </div>
              {p.overwriteManual ? (
                <label className="inline stage-ack">
                  <input type="checkbox" checked={p.overwriteAck} onChange={(e) => p.setOverwriteAck(e.target.checked)} />{' '}
                  I understand my {p.corrections} will be replaced
                </label>
              ) : (
                <p className="muted">Your {p.corrections} {p.manualCount === 1 ? 'is' : 'are'} kept.</p>
              )}
            </div>
          )}
          <div className="actions">
            <button
              type="button"
              className={buttonClass('ghost')}
              disabled={p.busy || p.needsAck || speakersMissing}
              aria-describedby={p.needsAck ? 'diarize-needed' : undefined}
              title={speakersMissing ? p.needs?.speakers[0]?.reason : undefined}
              onClick={p.onDiarize}
            >
              Detect speakers only
            </button>
            {p.needsAck ? (
              <span className="muted" id="diarize-needed">Still needed: tick the confirmation above, or turn Replace off.</span>
            ) : (
              p.hasMedia && <span className="muted" data-testid="diarize-estimate">{diarizeEstimate(p.duration, config?.measured_diarize_speed, config?.measured_diarize_runs)}</span>
            )}
          </div>
          {p.needs?.speakers.map((n) => <Fragment key={n.id}>{fix(n)}</Fragment>)}
          <SpeakerDetectionNotice dramaId={p.dramaId} active={!speakersMissing} />
          <DiarizationDeviceNote dramaId={p.dramaId} refreshKey={p.busy} />
        </>
      ),
    }),
    sensitivity_preset: () =>
      select('Sensitivity', 'sensitivity_preset', ['normal', 'sensitive'], 'Catches quieter or faster speech, but may add false text on music or breathing.'),
    min_silence_ms: () =>
      num('Min silence', 'min_silence_ms', 50, `${rangeHint(RANGES.min_silence_ms.min, RANGES.min_silence_ms.max, { unit: 'ms', default: MIN_SILENCE_MS_DEFAULT })} Silence that splits lines; longer gives fewer, longer lines. Lower values split at shorter pauses and can cut mid-sentence. Auto-tune below can pick it.`, 'ms', 'min_silence'),
    min_pause_sec: () =>
      num('Pause that can split a long line', 'min_pause_sec', 0.05, `${rangeHint(RANGES.min_pause_sec.min, RANGES.min_pause_sec.max, { unit: 's', default: MIN_PAUSE_SEC_DEFAULT })} Longer lines are only cut where the speaker pauses at least this long. Higher gives fewer, longer lines. Lower cuts more.`, 's'),
    split_by_sentences: () =>
      toggle(
        'Split lines by sentences',
        'split_by_sentences',
        'Whisper hears longer stretches of speech, then lines are cut at sentence ends and, for long ones, at pauses between words. Min silence is not used. Whisper and Qwen3 ASR only; the speech-detection backends already cut their own lines.'
          + (!['whisper', 'qwen3_asr'].includes(cf.asr_backend_choice)
            ? ' Not used with the selected ASR backend: choose Whisper or Qwen3 ASR for this to apply. Long lines can still be cut afterwards in Review.'
            : ''),
      ),
    realign_long_segments: () => toggle('Realign long segments', 'realign_long_segments', 'Experimental, off by default. Re-times each line against its own audio with the MMS aligner (needs torchaudio and uroman, about 1.1 GB on first use). It only changes timing, not the text.', undefined, p.needs?.realign),
    vad_threshold: () => num('VAD threshold', 'vad_threshold', 0.05, `${rangeHint(RANGES.vad_threshold.min, RANGES.vad_threshold.max, { default: VAD_THRESHOLD_DEFAULT })} Higher ignores more quiet sound.`, undefined, 'vad_threshold'),
    hardsub_interval_sec: () => num('Hardsub interval', 'hardsub_interval_sec', 0.1, `${rangeHint(RANGES.hardsub_interval_sec.min, RANGES.hardsub_interval_sec.max, { unit: 's', default: HARDSUB_INTERVAL_DEFAULT })} How often video frames are read for on-screen text.`, 's'),
    alignment_method: () =>
      select('Alignment method', 'alignment_method', ['whisper_diff', 'qwen3_forced_align'],
        haveTranscript
          ? 'Qwen3 forced alignment lines up the transcript you supply against the audio for more exact timing.'
          : 'Forced alignment lines up a transcript you provide; for raw audio, pick Whisper or Qwen3-ASR.',
        haveTranscript ? (p.needs?.qwenAlign ? ['qwen3_forced_align'] : []) : ['qwen3_forced_align'],
        haveTranscript && cf.alignment_method !== 'qwen3_forced_align' ? fix(p.needs?.qwenAlign) : null),
    asr_backend_choice: () =>
      select('ASR backend', 'asr_backend_choice', asrOptions, [asrBackendHelp(asrOptions), config?.asr_backend_notice].filter(Boolean).join('\n'),
        p.needs ? unavailableBackends(p.needs) : [],
        // The chosen backend's own need is in the Before-you-run list; this is for the options it rules out.
        QWEN3_BACKENDS.includes(cf.asr_backend_choice) ? null : fix(p.needs?.qwen3)),
    voice_detector: () => ({ zone: 'grid', node: <VoiceDetectorField /> }),
    use_groq: () => toggle('Use Groq', 'use_groq', GROQ_HELP, undefined, p.needs?.groq),
    whisper_fast_mode: () =>
      toggle(
        'Whisper fast mode',
        'whisper_fast_mode',
        "Off by default. Decodes several chunks of speech at once (about 2-4x faster on a GPU, uses more GPU memory). It can make lines fewer and longer than a normal run (Baihe's own test: 44 lines against 150, and a higher error rate); try Compare transcription first.",
        'fast_mode',
      ),
    vocabulary_hint: () =>
      toggle(
        'Name hint for Qwen3 ASR',
        'vocabulary_hint',
        "Gives Qwen3 ASR this title's character names and glossary terms as a spelling hint. Can pull a line toward a name that wasn't said, so check the lines after turning it on.",
      ),
    prompt: () => ({
      zone: 'block',
      node: (
        <>
          <p className="muted" data-testid="auto-prompt">
            {config?.auto_initial_prompt
              ? `Automatic prompt, from glossary and novel: ${config.auto_initial_prompt}`
              : 'Automatic prompt: no glossary names or raw novel yet. Extra names below still help.'}
          </p>
          <details className="prompt-fold">
            <summary>
              More: replace the automatic prompt
              {promptSummary(p.extraNames, p.override) && (
                <span className="section-summary" data-testid="prompt-summary">{promptSummary(p.extraNames, p.override)}</span>
              )}
            </summary>
            <Field label="Extra names to expect" help="Added to the automatic prompt. Separate names with 、 or commas.">
              <input value={p.extraNames} placeholder="沈清疑、云隐宗" onChange={(e) => p.setExtraNames(e.target.value)} />
            </Field>
            <Field label="Replacement prompt" help="Used instead of the automatic prompt and extra names. Leave empty to keep the automatic one.">
              <input value={p.override} onChange={(e) => p.setOverride(e.target.value)} />
            </Field>
          </details>
        </>
      ),
    }),
    beam_size: () => num('Beam size', 'beam_size', 1, `${rangeHint(RANGES.beam_size.min, RANGES.beam_size.max, { default: BEAM_SIZE_DEFAULT })} Higher is slower and a little more accurate.`, undefined, 'beam_size'),
    separation_backend: () => select('Separation backend', 'separation_backend', ['auto', 'audio_separator', 'demucs'], 'Used when vocals are separated first.'),
    hardsub_ocr_backend: () =>
      select('Hardsub OCR', 'hardsub_ocr_backend', ['tesseract', 'paddle', 'auto'], 'PaddleOCR reads Chinese, Korean and Japanese captions with the matching language model. Automatic uses it when installed and falls back to Tesseract, with a note.'),
    separate_vocals_first: () =>
      toggle(
        'Separate vocals first',
        'separate_vocals_first',
        'Removes background music before transcribing. Skip it unless the background is music alone: it hurt with noise and did nothing on clean audio. Fast on a GPU; on the CPU it adds a long wait (often many times the clip length).',
        undefined,
        p.needs?.separation,
      ),
    hallucination_silence_sec: () =>
      num('Hallucination guard', 'hallucination_silence_sec', 0.5, `Experimental. Off (0) by default: 0 or ${rangeHint(0.5, 10, { unit: 's' })} Titles that were at exactly 2.0, the old default, were reset to 0 once. Whisper skips a line with this much silence inside it, which stops invented text over silence or music. Lower is stricter and can drop real lines after a pause. Local Whisper only: ignored by Qwen3-ASR, Groq, Mixed languages and Fast mode.`, 's', 'hallucination_guard'),
    whisper_repeat_guard: () =>
      toggle(
        'Whisper repeat guard',
        'whisper_repeat_guard',
        'Stops Whisper repeating the same few words. Can drop or change real Chinese and Japanese speech, where short words repeat naturally. Turn on only if a title shows repeated-phrase loops.',
        'repeat_guard',
      ),
  }

  const summaries: Record<string, string> = {
    speakers: speakersSummary(p.speakers.expected, p.speakers.min, p.speakers.max),
    timing: `${optionLabel(cf.sensitivity_preset)} · ${cf.min_silence_ms} ms · ${cf.min_pause_sec} s`,
    models: optionLabel(cf.asr_backend_choice),
    guards: Number(cf.hallucination_silence_sec) === DEFAULT_HALLUCINATION_SILENCE_SEC ? 'off' : `hallucination guard ${cf.hallucination_silence_sec} s`,
    switches: switchesSummary(cf, developerMode),
  }

  return (
    <section className="panel" aria-label="Advanced options">
      <h3 className="source-subhead">Advanced options</h3>
      <p className="muted source-summary" data-testid="advanced-summary">
        {readableSummary(advancedSummary({ ...cf, prompt: p.override, source_language: language }, developerMode))}
      </p>
      {advancedGroups(developerMode, p.optionsSaved, p.kind).map((g) => {
        const placed = g.fields.map((f) => fields[f]())
        const grid = placed.filter((x) => x.zone === 'grid')
        const blocks = placed.filter((x) => x.zone === 'block')
        const list = placed.filter((x) => x.zone === 'list')
        return (
          <Section key={g.id} storageKey={g.storageKey} defaultOpen={g.defaultOpen} title={g.label} summary={summaries[g.id]}>
            {grid.length > 0 && <div className="source-grid">{grid.map((x, i) => <Fragment key={i}>{x.node}</Fragment>)}</div>}
            {blocks.map((x, i) => <Fragment key={i}>{x.node}</Fragment>)}
            {list.length > 0 && <div className={g.id === 'switches' ? 'setting-list source-switches' : 'setting-list'}>{list.map((x, i) => <Fragment key={i}>{x.node}</Fragment>)}</div>}
          </Section>
        )
      })}
      <div className="actions">
        <button type="button" className={buttonClass('secondary', 'sm')} onClick={p.onSave}>Save options</button>
        <button type="button" className={buttonClass('ghost', 'sm')} onClick={p.onReset}>Reset to defaults</button>
        {p.saved && <span role="status" className="muted">Saved.</span>}
      </div>
      {p.problem && <p className="error" role="alert">{p.problem}</p>}
      <ErrorBanner error={p.error} onDismiss={p.onDismissError} describe={{ serverText: true }} />
    </section>
  )
}

