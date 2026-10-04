/*
 * Whether Settings > Transcription experiments has the MOSS backend
 * on. False until known, and on any failure (another device without
 * admin.settings gets a 403 and simply doesn't see the choice).
 */
import { useEffect, useState } from 'react'

import { getAsrOptions } from '../api/asrOptions'

export function useMossExperimental(): boolean {
  const [on, setOn] = useState(false)
  useEffect(() => {
    let live = true
    getAsrOptions().then(
      (o) => live && setOn(o.moss_experimental),
      () => live && setOn(false),
    )
    return () => {
      live = false
    }
  }, [])
  return on
}
