// Low-data mode for the comic reader: smaller page pictures on a metered connection. Per device, so it is kept in this
// browser and never in the household's saved view settings.
import { usePersistedState } from '../../hooks/usePersistedState'

export type LowDataMode = 'auto' | 'on' | 'off'
export const LOW_DATA_OPTIONS: [LowDataMode, string][] = [['auto', 'Auto'], ['on', 'On'], ['off', 'Off']]

interface Connection { saveData?: boolean; type?: string }

// Chrome on Android reports the data-saver switch and a cellular type; other browsers report nothing, which counts as not metered.
export function isMetered(nav: { connection?: Connection } | undefined = typeof navigator === 'undefined' ? undefined : (navigator as { connection?: Connection })): boolean {
  const c = nav?.connection
  return !!c && (c.saveData === true || c.type === 'cellular')
}

export const lowDataActive = (mode: LowDataMode, metered: boolean) => mode === 'on' || (mode === 'auto' && metered)

export function useLowData(): { mode: LowDataMode; setMode: (m: LowDataMode) => void; active: boolean } {
  const [stored, setMode] = usePersistedState<string>('comic.lowData', 'auto')
  const mode: LowDataMode = stored === 'on' || stored === 'off' ? stored : 'auto'
  return { mode, setMode, active: lowDataActive(mode, isMetered()) }
}
