import { useState } from 'react'

import { updatePreferences } from '../../api/settings'
import { translateApi } from '../../api/translate'
import { ButtonLink } from '../../components/Button'
import { Card } from '../../components/Card'
import { ErrorBanner } from '../../components/ErrorBanner'
import { buttonClass } from '../../components/uiClasses'
import { useLoad } from '../../hooks/useLoad'
import type { PcMode } from '../../hooks/usePcOnly'
import { PreflightCard } from '../preflight/PreflightCard'
import { GET_STARTED_STEPS, initialTranslator, translatorOptions } from './getStartedLogic'

// The first-run card on an empty Library: the pipeline in five lines and a
// choice of translator. A key is never typed here (Settings holds that form).
export function GetStarted({ pc, onNew, onDismiss }: { pc: PcMode; onNew: () => void; onDismiss: () => void }) {
  const [reload, setReload] = useState(0)
  const engines = useLoad(translateApi.engineList, reload)
  const [picked, setPicked] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const list = engines.data?.items ?? []
  const options = translatorOptions(list)
  const current = picked ?? initialTranslator(list, engines.data?.default_engine ?? null)
  const chosen = options.find((o) => o.name === current)

  const save = () => {
    setBusy(true)
    setError(null)
    updatePreferences({ default_engine: current }).then(
      () => { setBusy(false); setSaved(current) },
      (err: unknown) => { setBusy(false); setError(err) },
    )
  }

  return (
    <Card
      title="Get started"
      meta="Five steps from a file to subtitles."
      className="get-started"
      aria-label="Get started"
      actions={<button type="button" className={buttonClass('ghost', 'sm')} onClick={onDismiss}>Dismiss</button>}
    >
      <ol className="get-started-steps">
        {GET_STARTED_STEPS.map((s) => (
          <li key={s.title}><strong>{s.title}.</strong> {s.text}</li>
        ))}
      </ol>
      <div className="actions">
        <button type="button" className={buttonClass('primary')} onClick={onNew}>New drama</button>
        <ButtonLink variant="secondary" href="#/discover">Discover</ButtonLink>
        <ButtonLink variant="secondary" href="#/sources">Sources</ButtonLink>
      </div>
      <fieldset className="get-started-translator">
        <legend>Translator</legend>
        <ErrorBanner error={engines.error} />
        {options.map((o) => (
          <label key={o.name} className="get-started-option">
            <input
              type="radio"
              name="get-started-translator"
              value={o.name}
              checked={o.name === current}
              onChange={() => { setPicked(o.name); setSaved(null) }}
            />
            <span className="get-started-option-text">
              <span><strong>{o.label}</strong> <span className={o.ready ? 'muted' : 'warn'}>{o.readyText}</span></span>
              <span className="muted">{o.needs}</span>
            </span>
          </label>
        ))}
        {chosen && !chosen.ready && (
          <>
            <p className="muted" data-testid="translator-needs-key">{chosen.label} can't run until a key is added.</p>
            <PreflightCard
              needs={['key']} engine={current}
              onReady={(ok) => ok && setReload((n) => n + 1)}
              onUseEngine={(name) => { setPicked(name); setSaved(null) }}
            />
          </>
        )}
        {chosen && (pc === 'remote'
          ? <p className="muted">Changing the translator is PC only.</p>
          : (
            <div className="actions">
              <button type="button" className={buttonClass('secondary')} disabled={busy || saved === current} onClick={save}>
                {saved === current ? 'Saved' : `Use ${chosen.label} for new dramas`}
              </button>
            </div>
          ))}
        <ErrorBanner error={error} />
      </fieldset>
    </Card>
  )
}
