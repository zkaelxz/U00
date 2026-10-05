/*
 * Settings > Transcription experiments (Steps 103/104). Both are off by
 * default and PC only:
 *   - Qwen3-ASR batch size: how many lines go to Qwen3-ASR at once when a
 *     drama's ASR backend is Qwen3 ASR. 1 is the original one-at-a-time run.
 *   - Mixed languages: detect the spoken language per speech span.
 *   - MOSS-Transcribe-Diarize: lets the experimental one-pass transcribe +
 *     speakers backend be picked in a drama's Transcribe > Advanced.
 * Another device sees "PC only" and makes no calls.
 */
import { useEffect, useState } from 'react'

import { batchingNote, getAsrOptions, parseBatchSize, updateAsrOptions, type AsrOptions, type AsrOptionsUpdate } from '../../api/asrOptions'
import { getPcMode, loadPcMode } from '../../api/pcOnly'
import { Badge } from '../../components/Badge'
import { Card } from '../../components/Card'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { Toggle } from '../../components/Toggle'
import { buttonClass } from '../../components/uiClasses'
import { PC_ONLY_BODY, PC_ONLY_SUMMARY, usePcOnly } from '../../hooks/usePcOnly'

const TITLE = 'Transcription experiments'

export function TranscriptionExperimentsCard() {
  const pc = usePcOnly()
  if (pc === 'remote') {
    return (
      <Card title={TITLE} meta={PC_ONLY_SUMMARY} aria-label={TITLE}>
        <p className="muted">{PC_ONLY_BODY}</p>
      </Card>
    )
  }
  return <Controls />
}

function Controls() {
  const [opts, setOpts] = useState<AsrOptions | null>(null)
  const [batch, setBatch] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let live = true
    void loadPcMode().then(() => {
      if (!live || getPcMode() === 'remote') return
      getAsrOptions().then(
        (o) => {
          if (!live) return
          setOpts(o)
          setBatch(String(o.qwen_asr_batch_size))
        },
        (e: unknown) => live && setError(e),
      )
    })
    return () => {
      live = false
    }
  }, [])

  const save = (update: AsrOptionsUpdate) => {
    setSaving(true)
    setError(null)
    updateAsrOptions(update).then(
      (o) => {
        setOpts(o)
        setBatch(String(o.qwen_asr_batch_size))
        setSaving(false)
      },
      (e: unknown) => {
        setError(e)
        setSaving(false)
      },
    )
  }

  if (!opts) {
    return (
      <Card title={TITLE} aria-label={TITLE}>
        {error ? <ErrorBanner error={error} /> : <p className="muted">Loading…</p>}
      </Card>
    )
  }

  const parsed = parseBatchSize(batch, opts.qwen_asr_batch_min, opts.qwen_asr_batch_max)
  const batchError = parsed === null ? `A whole number from ${opts.qwen_asr_batch_min} to ${opts.qwen_asr_batch_max}.` : undefined
  const meta = opts.moss_experimental || opts.qwen_vad_refine_timing || opts.mixed_languages || opts.qwen_asr_batch_size > 1 ? 'On' : 'Off'

  return (
    <Card title={TITLE} meta={meta} aria-label={TITLE}>
      {error ? <ErrorBanner error={error} /> : null}
      <div className="settings-form">
        <Field
          label="Qwen3-ASR batch size"
          help="Lines sent to Qwen3-ASR at once when a drama uses the Qwen3 ASR backend. 1 sends one at a time (the tested way). Higher can be faster on a GPU but is not yet checked against real audio; compare the text before relying on it."
          error={batchError}
        >
          <input
            type="number"
            inputMode="numeric"
            min={opts.qwen_asr_batch_min}
            max={opts.qwen_asr_batch_max}
            value={batch}
            onChange={(e) => setBatch(e.target.value)}
            aria-label="Qwen3-ASR batch size"
          />
        </Field>
        <p className="muted" data-testid="qwen-batching-note">
          {batchingNote(opts)}
        </p>
        <div className="settings-actions">
          <button
            type="button"
            className={buttonClass('secondary')}
            disabled={saving || parsed === null || parsed === opts.qwen_asr_batch_size}
            onClick={() => parsed !== null && save({ qwen_asr_batch_size: parsed })}
          >
            Save batch size
          </button>
        </div>
        <div className="setting-list">
          <Field
            label="Refine line timing with the forced aligner"
            help="For the Qwen3 ASR with speech detection backend: after transcribing, Qwen3-ForcedAligner tightens each line's start and end inside its speech span. Slower. Lines whose timing had to be estimated are flagged. Off uses the speech spans as they are."
          >
            <Toggle
              checked={opts.qwen_vad_refine_timing}
              disabled={saving}
              onChange={(next) => save({ qwen_vad_refine_timing: next })}
              aria-label="Refine line timing with the forced aligner"
            />
          </Field>
          <Field
            label="Mixed languages"
            help="For a video where people speak more than one language (say Korean, Chinese and Japanese). The language is detected for each stretch of speech, and a line whose language differs from the title's is marked in Review. Runs with the Whisper and Qwen3 ASR with speech detection backends; the speech detection backend has Qwen3-ASR detect the language itself (Chinese, Japanese, Korean or English). Slower: one language detection per stretch of speech. With the speech detection backend, refining line timing is skipped. Off transcribes everything in the title's language."
          >
            <Toggle
              checked={opts.mixed_languages}
              disabled={saving}
              onChange={(next) => save({ mixed_languages: next })}
              aria-label="Mixed languages"
            />
          </Field>
          <Field
            label="MOSS-Transcribe-Diarize (experimental)"
            help="Adds MOSS as an ASR backend choice in a drama's Transcribe > Advanced. It transcribes and labels speakers in one pass. Not yet compared with Whisper and pyannote on audio dramas. It runs model code downloaded from Hugging Face (a pinned version) inside this app, and once on, anyone allowed to edit lines and start jobs can use it."
          >
            <Toggle
              checked={opts.moss_experimental}
              disabled={saving}
              onChange={(next) => save({ moss_experimental: next })}
              aria-label="MOSS-Transcribe-Diarize (experimental)"
            />
          </Field>
        </div>
        <p className="muted" data-testid="moss-installed">
          <Badge tone={opts.moss_installed ? 'ok' : 'neutral'}>{opts.moss_installed ? 'Installed' : 'Not installed'}</Badge>{' '}
          {opts.moss_installed
            ? 'The moss_transcribe_diarize package is installed on this PC.'
            : 'Install it from its GitHub repository (see docs/asr-experiments.md). It upgrades Transformers to 5, which stops Qwen3-ASR working.'}
        </p>
      </div>
    </Card>
  )
}
