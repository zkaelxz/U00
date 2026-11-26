/** A followed channel's own Live settings, edited in place: what its Start Live (and an automatic start) runs with. */
import { useState, type FormEvent } from 'react'

import { languageWhisperDefault } from '../../api/live'
import { MAX_FOLLOW_TERMS, parseTerms, termsToText } from '../../api/liveFollows'
import { engineShortName } from '../../api/translate'
import { Field } from '../../components/Field'
import { ModelSelect } from '../../components/ModelSelect'
import { buttonClass } from '../../components/uiClasses'
import { AI_ENGINE_LABEL } from '../../helpText'
import type { LiveFollowOptions, LiveFormOptions } from '../../types/live'
import type { TranslateEngine } from '../../types/translate'
import { whisperExclusionReason } from '../workspace/sourceForm'

type Props = {
  name: string
  saved: LiveFollowOptions
  /** The Live form's current options, which "Copy from the Live form" fills in. */
  fromForm: LiveFollowOptions
  opts: LiveFormOptions
  /** Engines with a key; null while they load. */
  engines: TranslateEngine[] | null
  /** Why Whisper can't run on the GPU here, or null. */
  gpuReason: string | null
  /** Any request on the card is running: nothing can be pressed. */
  busy: boolean
  /** This panel's own save is running. */
  saving: boolean
  onSave: (options: LiveFollowOptions) => void
  onCancel: () => void
}

// Blank (or not a number) means no time limit, as on the Live form.
const minutesLimit = (n: number | null | undefined, [lo, hi]: readonly number[]) =>
  n == null || !Number.isFinite(n) ? null : Math.min(hi, Math.max(lo, Math.round(n)))

export function FollowSettings({ name, saved, fromForm, opts, engines, gpuReason, busy, saving, onSave, onCancel }: Props) {
  const [draft, setDraft] = useState<LiveFollowOptions>(saved)
  const [termsText, setTermsText] = useState(() => termsToText(saved.terms))
  const parsedTerms = parseTerms(termsText)
  const set = <K extends keyof LiveFollowOptions>(k: K, v: LiveFollowOptions[K]) => setDraft((d) => ({ ...d, [k]: v }))
  const language = draft.source_language ?? opts.defaults.source_language
  // A saved or copied model this language can't use (Start would refuse it) gives way to the language's default.
  const picked = draft.whisper_size ?? languageWhisperDefault(opts, language)
  const whisper = whisperExclusionReason(opts.whisper_exclusions, picked, language) ? languageWhisperDefault(opts, language) : picked
  // A saved engine of null is the Live default (Ollama on this PC).
  const engine = draft.engine || opts.default_engine
  const usable = engines ?? []
  const engineEntry = usable.find((e) => e.name === engine)
  const savedEngineGone = !!engines && !engineEntry

  const changeLanguage = (lang: string) => {
    // A model this language can't use gives way to the one the form would start on.
    const keep = !whisperExclusionReason(opts.whisper_exclusions, whisper, lang)
    setDraft((d) => ({ ...d, source_language: lang, whisper_size: keep ? whisper : languageWhisperDefault(opts, lang) }))
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (parsedTerms.error) return
    onSave({
      ...draft,
      source_language: language,
      whisper_size: whisper,
      engine: engine === opts.default_engine ? null : engine,
      model: draft.model || null,
      use_gpu: !!draft.use_gpu && !gpuReason,
      max_minutes: minutesLimit(draft.max_minutes, opts.max_minutes_range),
      terms: parsedTerms.terms,
    })
  }

  return (
    <form className="live-follow-settings-form" onSubmit={submit} aria-label={`Settings for ${name}`} noValidate>
      <div className="field-row">
        <Field label="Language">
          <select value={language} disabled={busy} onChange={(e) => changeLanguage(e.target.value)}>
            {opts.languages.map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}
          </select>
        </Field>
        <Field label="Whisper model" help="The caption model. Changing the language picks the model that suits it, when one is on this PC.">
          <select value={whisper} disabled={busy} onChange={(e) => set('whisper_size', e.target.value)}>
            {opts.whisper_sizes.map((w) => {
              const wrong = !!whisperExclusionReason(opts.whisper_exclusions, w, language)
              return <option key={w} value={w} disabled={wrong}>{w}{wrong ? ' (not for this language)' : ''}</option>
            })}
          </select>
        </Field>
        <Field label={AI_ENGINE_LABEL} help="Engines without a key are hidden; add keys in Settings.">
          <select value={engine} disabled={busy || !usable.length} onChange={(e) => setDraft((d) => ({ ...d, engine: e.target.value, model: null }))}>
            {savedEngineGone && <option value={engine}>{engineShortName({ name: engine })} (no key)</option>}
            {usable.map((en) => <option key={en.name} value={en.name}>{engineShortName(en)}{en.free ? '' : ' (paid)'}</option>)}
          </select>
        </Field>
        <ModelSelect engine={engineEntry} value={draft.model ?? ''} disabled={busy} onChange={(m) => set('model', m || null)} />
        <Field label="Stop after" unit="min" help="A hard cap on how long a session from this channel runs (and spends). Leave it blank to run until the stream ends.">
          <input type="number" min={opts.max_minutes_range[0]} max={opts.max_minutes_range[1]} step={1} disabled={busy} placeholder="no limit"
            value={draft.max_minutes != null && Number.isFinite(draft.max_minutes) ? draft.max_minutes : ''}
            onChange={(e) => set('max_minutes', Number.isFinite(e.target.valueAsNumber) ? e.target.valueAsNumber : null)} />
        </Field>
      </div>
      <label className="live-check">
        <input type="checkbox" checked={!!draft.use_gpu && !gpuReason} disabled={busy || !!gpuReason} onChange={(e) => set('use_gpu', e.target.checked)} />
        Use GPU for Whisper
      </label>
      {gpuReason && <p className="muted">{gpuReason}</p>}
      <Field label="Words for this channel" help={`Names and terms this channel's Live always translates the same way, one per line as original = translation (up to ${MAX_FOLLOW_TERMS}). Used on top of any glossary above, and the PC's captions listen for them too.`}>
        <textarea rows={4} value={termsText} disabled={busy} placeholder="高市 = Takaichi" onChange={(e) => setTermsText(e.target.value)} />
      </Field>
      {parsedTerms.error && <p className="muted" role="alert">{parsedTerms.error}</p>}
      <div className="actions">
        <button type="submit" className={buttonClass('primary', 'sm')} disabled={busy || !!parsedTerms.error}>{saving ? 'Saving…' : 'Save settings'}</button>
        <button type="button" className={buttonClass('ghost', 'sm')} disabled={busy} onClick={() => setDraft({ ...fromForm, terms: draft.terms })}
          title="Fill these in from the Live form above. Nothing is saved until Save settings.">
          Copy from the Live form
        </button>
        <button type="button" className={buttonClass('ghost', 'sm')} disabled={busy} onClick={onCancel}>Cancel</button>
      </div>
    </form>
  )
}
