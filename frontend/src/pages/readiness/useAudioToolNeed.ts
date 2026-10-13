import { useReadiness } from '../../hooks/useReadiness'
import { audioToolNeed, type Need } from './needs'

/** What a tool that re-hears the audio (Re-transcribe, Auto-tune) or scans it for speech (Timing check, Speech coverage) lacks; null while unknown. */
export function useAudioToolNeed(needsFfmpeg = false): Need | null {
  const { readiness } = useReadiness()
  return audioToolNeed(readiness, needsFfmpeg)
}
