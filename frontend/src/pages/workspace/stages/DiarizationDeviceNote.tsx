/*
 * One muted line under Speakers saying where the last speaker
 * detection ran (GPU or CPU), from GET /api/diarization/dramas/{id}/config.
 * Nothing is shown before a run has recorded a device, or if the read fails.
 */
import { useEffect, useState } from 'react'

import { deviceNote, getDiarizationConfig } from '../../../api/asrOptions'

export function DiarizationDeviceNote({ dramaId, refreshKey }: { dramaId: number; refreshKey?: unknown }) {
  const [note, setNote] = useState<string | null>(null)
  useEffect(() => {
    let live = true
    getDiarizationConfig(dramaId).then(
      (c) => live && setNote(deviceNote(c.last_device)),
      () => live && setNote(null),
    )
    return () => {
      live = false
    }
  }, [dramaId, refreshKey])
  return note ? (
    <p className="muted" data-testid="diarize-device">
      {note}
    </p>
  ) : null
}
