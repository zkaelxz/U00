/*
 * Settings > Transcription experiments (Steps 103/104). Both are off by
 * default and PC only:
 *   - Qwen3-ASR batch size: how many lines go to Qwen3-ASR at once when a
 *     drama's ASR backend is Qwen3 ASR. 1 is the original one-at-a-time run.
 *   - MOSS-Transcribe-Diarize: lets the experimental one-pass transcribe +
 *     speakers backend be picked in a drama's Transcribe > Advanced.
 * Another device sees "PC only" and makes no calls.
 */
import { useEffect, useState } from 'react'

import { getAsrOptions, parseBatchSize, updateAsrOptions, type AsrOptions, type AsrOptionsUpdate } from '../../api/asrOptions'
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
  const meta = opts.moss_experimental || opts.qwen_asr_batch_size > 1 ? 'On' : 'Off'

  return (
    <Card title={TITLE} meta={meta} aria-label={TITLE}>
      {error ? <ErrorBanner error={error} /> : null}
      <div className="setting-list">
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
        <div className="actions">
          <button
            type="button"
            className={buttonClass('secondary')}
            disabled={saving || parsed === null || parsed === opts.qwen_asr_batch_size}
            onClick={() => parsed !== null && save({ qwen_asr_batch_size: parsed })}
          >
            Save batch size
          </button>
        </div>
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
