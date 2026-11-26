/** The Live form's remembered options for this browser, and how a language change treats the remembered Whisper model. */
import { useEffect } from 'react'

import { DEFAULT_FORM, type LiveOptions, type StoredLiveOptions } from '../../api/live'
import type { LiveFormOptions } from '../../types/live'
import { usePersistedState } from '../../hooks/usePersistedState'
import { whisperExclusionReason } from '../workspace/sourceForm'

/** Chunk figures saved while 10 s / 3 s was every language's default. Kept, they hide the shorter Chinese
 * default for good, so a browser still holding exactly that pair is let follow the defaults again. */
export function withoutStaleChunkPrefs(prefs: Partial<StoredLiveOptions>): Partial<StoredLiveOptions> {
  if (prefs.segment_seconds !== 10 || prefs.overlap_seconds !== 3) return prefs
  const { segment_seconds: _s, overlap_seconds: _o, ...rest } = prefs
  return rest
}

export function useLivePrefs(opts: LiveFormOptions) {
  // Only the options this browser changed, so the rest follow the server's defaults.
  const [prefs, setPrefs] = usePersistedState<Partial<StoredLiveOptions>>('live.options', {})
  // An earlier version saved the form's own default model as if it were a pick whenever the
  // language changed, which kept 'small' from following the language. Dropped once; a model
  // picked since then is kept.
  const [checked, setChecked] = usePersistedState<boolean>('live.whisperPrefChecked', false)
  useEffect(() => {
    if (checked) return
    setChecked(true)
    if (prefs.whisper_size === DEFAULT_FORM.whisper_size) {
      const { whisper_size: _stale, ...rest } = prefs
      setPrefs(rest)
    }
  }, [checked, setChecked, prefs, setPrefs])

  const [chunkChecked, setChunkChecked] = usePersistedState<boolean>('live.chunkPrefChecked', false)
  useEffect(() => {
    if (chunkChecked) return
    setChunkChecked(true)
    const kept = withoutStaleChunkPrefs(prefs)
    if (kept !== prefs) setPrefs(kept)
  }, [chunkChecked, setChunkChecked, prefs, setPrefs])

  const setOpt = <K extends keyof LiveOptions>(k: K, v: LiveOptions[K]) => setPrefs({ ...prefs, [k]: v })
  // A model this browser picked stays across languages unless Start would refuse it; otherwise
  // the model is left unset so it follows the new language's default.
  const changeLanguage = (language: string) => {
    const { whisper_size: picked, ...rest } = prefs
    const keep = picked !== undefined && !whisperExclusionReason(opts.whisper_exclusions, picked, language)
    setPrefs({ ...rest, source_language: language, ...(keep ? { whisper_size: picked } : {}) })
  }
  return { prefs, setPrefs, setOpt, changeLanguage }
}
