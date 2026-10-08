/*
 * "Voice detector" choice in Transcribe > Advanced. It is one app-wide setting
 * (GET/POST /api/settings/asr-options), not a per-title one, so changing it
 * changes it for every title; Auto uses the ASMR detector for ASMR titles.
 * The ASMR model is a ~119 MB download that only starts from the button here.
 * Saving and downloading are PC only; another device sees the current choice.
 */
import { useEffect, useState } from 'react'

import {
  getAsrOptions,
  startVoiceDetectorDownload,
  updateAsrOptions,
  VOICE_DETECTOR_LABELS,
  voiceDetectorNote,
  type AsrOptions,
  type VoiceDetector,
} from '../../../api/asrOptions'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { buttonClass } from '../../../components/uiClasses'
import { usePcOnly } from '../../../hooks/usePcOnly'

const HELP =
  'Finds where speech is before Qwen3-ASR transcribes it, in the "with speech detection" and long-window backends (Whisper uses its own). ' +
  'The ASMR detector is tuned for Japanese ASMR (whispers and soft speech) and has not been checked on Chinese; Standard stays available.'
const POLL_MS = 3000

export function VoiceDetectorField() {
  const pc = usePcOnly()
  const [opts, setOpts] = useState<AsrOptions | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [downloading, setDownloading] = useState(false)

  useEffect(() => {
    let live = true
    const read = () =>
      getAsrOptions().then(
        (o) => {
          if (!live) return
          setOpts(o)
          if (o.asmr_vad_model_downloaded) setDownloading(false)
        },
        () => undefined,
      )
    void read()
    const timer = downloading ? window.setInterval(read, POLL_MS) : undefined
    return () => {
      live = false
      if (timer) window.clearInterval(timer)
    }
  }, [downloading])

  if (!opts) return null
  const save = (voice_detector: VoiceDetector) =>
    updateAsrOptions({ voice_detector }).then(
      (o) => {
        setOpts(o)
        setError(null)
      },
      setError,
    )
  const download = () =>
    startVoiceDetectorDownload().then(() => {
      setError(null)
      setDownloading(true)
    }, setError)
  const note = voiceDetectorNote(opts)
  const canDownload = pc !== 'remote' && opts.voice_detector !== 'standard' && !opts.asmr_vad_model_downloaded

  return (
    <Field label="Voice detector" help={HELP}>
      <select
        value={opts.voice_detector}
        disabled={pc === 'remote'}
        onChange={(e) => void save(e.target.value as VoiceDetector)}
        aria-label="Voice detector"
      >
        {(Object.keys(VOICE_DETECTOR_LABELS) as VoiceDetector[]).map((v) => (
          <option key={v} value={v}>
            {VOICE_DETECTOR_LABELS[v]}
          </option>
        ))}
      </select>
      {error ? <ErrorBanner error={error} /> : null}
      {note ? (
        <p className="muted" data-testid="voice-detector-note">
          {downloading ? 'Downloading the ASMR detector model… this can take a few minutes.' : note}
        </p>
      ) : null}
      {canDownload && opts.asmr_vad_onnxruntime_installed ? (
        <button type="button" className={buttonClass('secondary')} disabled={downloading} onClick={() => void download()}>
          Download the ASMR detector (about 119 MB)
        </button>
      ) : null}
    </Field>
  )
}
