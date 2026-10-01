/*
 * The two choices that shape the Source stage, each next to what it changes
 * and saved as soon as it is picked (POST /api/source/dramas/{id}/config):
 *   TranscriptModePicker  where the lines come from (top of Transcribe)
 *   ContentModeField      how the audio is used (in Edit details)
 */
import { useEffect, useState } from 'react'

import { getSourceConfig, updateSourceConfig } from '../../../api/source'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import type { SourceConfig, SourceConfigUpdate } from '../../../types/workspace'
import { CONTENT_MODES, modeLabel } from '../detailsForm'
import { useStage } from '../StageContext'

const TRANSCRIPT_CHOICES: Record<string, string> = {
  have_transcript: 'I have a transcript',
  whisper: 'Transcribe the audio',
  hardsub_ocr: 'Read burned-in subtitles',
}

// Loads the config, and saves one change at a time, reporting what it saved.
function useSourceConfig(onSaved?: (c: SourceConfig) => void) {
  const { dramaId, refetchDrama } = useStage()
  const [config, setConfig] = useState<SourceConfig | null>(null)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    let cancelled = false
    getSourceConfig(dramaId).then(
      (c) => !cancelled && setConfig(c),
      (e: unknown) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId])

  const save = (update: SourceConfigUpdate) =>
    updateSourceConfig(dramaId, update).then(
      (c) => {
        setError(null)
        setConfig(c)
        refetchDrama() // streamer_vod also sets media_type
        onSaved?.(c)
      },
      setError,
    )

  return { config, error, setError, save }
}

export function TranscriptModePicker({ onChanged }: { onChanged: (mode: string) => void }) {
  const { dramaId } = useStage()
  const { config, error, setError, save } = useSourceConfig((c) => onChanged(c.transcript_mode))
  if (!config) return <ErrorBanner error={error} onDismiss={() => setError(null)} />
  const options = config.transcript_mode_options.includes(config.transcript_mode)
    ? config.transcript_mode_options
    : [config.transcript_mode, ...config.transcript_mode_options]
  return (
    <>
      <div className="segmented source-from" role="radiogroup" aria-label="Where the lines come from">
        {options.map((m) => {
          const needsVideo = m === 'hardsub_ocr' && !config.has_video_source
          return (
            <label
              key={m}
              className={m === config.transcript_mode ? 'segmented-on' : undefined}
              title={needsVideo ? 'Needs a source video: upload one first.' : undefined}
            >
              <input
                type="radio"
                name={`transcript-mode-${dramaId}`}
                checked={m === config.transcript_mode}
                disabled={needsVideo}
                onChange={() => void save({ transcript_mode: m })}
              />
              {TRANSCRIPT_CHOICES[m] ?? modeLabel(m)}
            </label>
          )
        })}
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </>
  )
}

export function ContentModeField() {
  const { config, error, setError, save } = useSourceConfig()
  if (!config) return <ErrorBanner error={error} onDismiss={() => setError(null)} />
  const options = CONTENT_MODES.includes(config.content_mode) ? CONTENT_MODES : [config.content_mode, ...CONTENT_MODES]
  return (
    <>
      <Field label="Content mode" help="How the audio is used. Streamer VOD also sets the media type to streamer vod. Saved when you pick it.">
        <select value={config.content_mode} onChange={(e) => void save({ content_mode: e.target.value })}>
          {options.map((m) => (
            <option key={m} value={m}>{modeLabel(m)}</option>
          ))}
        </select>
      </Field>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </>
  )
}
