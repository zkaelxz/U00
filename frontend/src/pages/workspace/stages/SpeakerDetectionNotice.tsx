/*
 * Says, before a run, why speaker detection cannot run (no Hugging Face token,
 * pyannote not installed), from GET /api/diarization/dramas/{id}/config. It never
 * blocks the run; a failed read shows nothing.
 */
import { useEffect, useState } from 'react'

import { getDiarizationConfig } from '../../../api/asrOptions'
import { buttonClass } from '../../../components/uiClasses'

export function SpeakerDetectionNotice({ dramaId, active }: { dramaId: number; active: boolean }) {
  const [notice, setNotice] = useState<string | null>(null)
  useEffect(() => {
    if (!active) return
    let live = true
    getDiarizationConfig(dramaId).then(
      (c) => live && setNotice(c.speaker_notice ?? null),
      () => live && setNotice(null),
    )
    return () => {
      live = false
    }
  }, [dramaId, active])
  if (!active || !notice) return null
  return (
    <p className="warn" role="note" data-testid="speaker-notice">
      {notice}{' '}
      <a href="#/settings" className={buttonClass('secondary', 'sm')}>Open Settings</a>
    </p>
  )
}
