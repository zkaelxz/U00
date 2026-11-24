/** The Live form's remembered options for this browser, and how a language change treats the remembered Whisper model. */
import { useEffect } from 'react'

import { DEFAULT_FORM, type LiveOptions, type StoredLiveOptions } from '../../api/live'
import type { LiveFormOptions } from '../../types/live'
import { usePersistedState } from '../../hooks/usePersistedState'
import { whisperExclusionReason } from '../workspace/sourceForm'

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
