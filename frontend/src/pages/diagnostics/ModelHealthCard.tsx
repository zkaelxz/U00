import { useCallback, useEffect, useState } from 'react'

import { checkModelProviders, clearModelOverride, getModelStatus, setModelOverride, setOfferProviderModels, switchPresetModel, type ModelStatus, type ModelStatusItem } from '../../api/models'
import { Badge } from '../../components/Badge'
import { ButtonLink } from '../../components/Button'
import { ConfirmButton } from '../../components/ConfirmButton'
import { Section } from '../../components/Section'
import { Toggle } from '../../components/Toggle'
import { buttonClass } from '../../components/uiClasses'
import type { PcMode } from '../../hooks/usePcOnly'
import {
  canChooseModel, compareHref, engineCheckLines, healthBadge, kindHelp, lastCheckedLine, modelHealthError, modelStatusLoadError, modelStatusLabel,
  modelStatusTone, noCandidatesLine, offerModelsNote, OFFER_MODELS_HELP, OFFER_MODELS_LABEL, overrideLine, splitModelItems, whereLabel,
} from './modelHealth'

/**
 * "Model health" (Step 40): every model this app is set up to use (built-in
 * defaults, workflow tiers, saved presets), with the ones that are retired,
 * deprecated, no longer listed or older shown first. The provider check is a
 * button, never automatic, and PC only; so is switching a preset or choosing
 * another model for a built-in default or tier, which ask for a second press.
 * Nothing switches by itself.
 */
export function ModelHealthCard({ pc }: { pc: PcMode }) {
  const [status, setStatus] = useState<ModelStatus | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const load = useCallback(() => {
    getModelStatus().then(
      (s) => {
        setStatus(s)
        setLoadError(null)
      },
      (e: unknown) => setLoadError(modelStatusLoadError(e)),
    )
  }, [])
  useEffect(load, [load])

  const check = () => {
    setBusy('check')
    setError(null)
    setNotice(null)
    checkModelProviders().then(
      (s) => {
        setStatus(s)
        setBusy(null)
        setNotice('Checked. The list below is up to date.')
      },
      (e: unknown) => {
        setBusy(null)
        setError(modelHealthError(e))
      },
    )
  }

  const toggleOffer = (on: boolean) => {
    setBusy('offer')
    setError(null)
    setNotice(null)
    setOfferProviderModels(on).then(
      () => {
        setBusy(null)
        load()
      },
      (e: unknown) => {
        setBusy(null)
        setError(modelHealthError(e))
      },
    )
  }

  const switchPreset = (item: ModelStatusItem) => {
    if (item.preset_id == null || !item.replacement) return
    const to = item.replacement
    setBusy(`switch:${item.preset_id}`)
    setError(null)
    setNotice(null)
    switchPresetModel(item.preset_id, item.model, to).then(
      () => {
        setBusy(null)
        setNotice(`${item.where} now uses ${to}.`)
        load()
      },
      (e: unknown) => {
        setBusy(null)
        setError(modelHealthError(e))
        // A stale row (409) or a deleted preset (404): show what is there now.
        load()
      },
    )
  }

  const chooseModel = (item: ModelStatusItem, to: string) => {
    if (!item.key || (item.kind !== 'default' && item.kind !== 'tier')) return
    setBusy(`choose:${item.kind}|${item.key}`)
    setError(null)
    setNotice(null)
    setModelOverride(item.kind, item.key, item.model, to).then(
      () => {
        setBusy(null)
        setNotice(`${whereLabel(item)} now uses ${to}.`)
        load()
      },
      (e: unknown) => {
        setBusy(null)
        setError(modelHealthError(e))
        load()
      },
    )
  }

  const restoreBuiltIn = (item: ModelStatusItem) => {
    if (!item.key || (item.kind !== 'default' && item.kind !== 'tier')) return
    setBusy(`choose:${item.kind}|${item.key}`)
    setError(null)
    setNotice(null)
    clearModelOverride(item.kind, item.key).then(
      () => {
        setBusy(null)
        setNotice(`${whereLabel(item)} uses the built-in ${item.builtin_model ?? 'model'} again.`)
        load()
      },
      (e: unknown) => {
        setBusy(null)
        setError(modelHealthError(e))
        load()
      },
    )
  }

  const canAct = pc !== 'remote'
  const badge = status ? healthBadge(status) : null
  const { attention, others } = status ? splitModelItems(status.items) : { attention: [], others: [] }
  const checks = status ? engineCheckLines(status.engines_checked) : []

  const problems = status ? status.warnings : 0
  const summary = status ? `${badge?.text ?? ''} · ${lastCheckedLine(status.checked_at)}` : 'Loading…'

  return (
    <Section title="Model health" storageKey="diagnostics.modelHealth" summary={summary} defaultOpen={problems > 0}>
      <div className="model-health" role="region" aria-label="Model health">
      <div className="actions">
        {badge && (
          <span data-testid="model-health-badge">
            <Badge tone={badge.tone}>{badge.text}</Badge>
          </span>
        )}
        <span className="muted" data-testid="model-health-checked">{status ? lastCheckedLine(status.checked_at) : 'Loading…'}</span>
        {canAct && (
          <button type="button" className={buttonClass('secondary', 'sm')} disabled={busy !== null} onClick={check}>
            {busy === 'check' ? 'Checking…' : 'Check providers now'}
          </button>
        )}
      </div>
      {loadError && (
        <p className="error" role="alert">
          {loadError}
        </p>
      )}
      {status && (
        <>
          <p className="muted">
            Nothing switches automatically: a preset only changes when you confirm it here.{' '}
            {canAct
              ? 'Checking providers asks each engine with a key for its model list; it never runs by itself.'
              : 'Checking providers and switching presets are PC only.'}
          </p>

          {canAct && (
            <div className="model-offer">
              <label htmlFor="offer-provider-models">
                <strong>{OFFER_MODELS_LABEL}</strong>
              </label>{' '}
              <Toggle
                id="offer-provider-models"
                checked={!!status.offer_provider_models}
                disabled={busy !== null}
                onChange={toggleOffer}
                aria-describedby="offer-provider-models-help"
              />
              <p className="muted" id="offer-provider-models-help">
                {OFFER_MODELS_HELP}
              </p>
              {offerModelsNote(status) && <p data-testid="model-offer-note">{offerModelsNote(status)}</p>}
            </div>
          )}

          {checks.length > 0 && (
            <ul className="model-checks" aria-label="Last provider check">
              {checks.map((c) => (
                <li key={c.engine} className={c.ok ? undefined : 'warn'}>
                  <strong>{c.label}:</strong> {c.text}
                  {c.detail && (
                    <details>
                      <summary>Details</summary>
                      <span className="muted">{c.detail}</span>
                    </details>
                  )}
                </li>
              ))}
            </ul>
          )}
          {status.checked_at && checks.length === 0 && (
            <p className="muted">No provider was checked: only Claude, Gemini and DeepSeek can be asked, and each needs a key in Settings.</p>
          )}

          {attention.length > 0 ? (
            <ul className="model-list" aria-label="Models that need attention">
              {attention.map((item) => (
                <ModelRow
                  key={rowKey(item)}
                  item={item}
                  canAct={canAct}
                  busy={busy}
                  checkedAt={status.checked_at}
                  onSwitch={() => switchPreset(item)}
                  onChoose={(to) => chooseModel(item, to)}
                  onUseBuiltIn={() => restoreBuiltIn(item)}
                />
              ))}
            </ul>
          ) : (
            <p data-testid="model-health-ok">No configured model is retired, deprecated or older.</p>
          )}

          {others.length > 0 && (
            <Section
              title={attention.length ? 'Other configured models' : 'All configured models'}
              count={others.length}
              storageKey="diagnostics.modelHealthOthers"
              summary={othersSummary(others)}
            >
              <ul className="model-list compact" aria-label="Other configured models">
                {others.map((item) => (
                  <ModelRow
                    key={rowKey(item)}
                    item={item}
                    canAct={canAct}
                    busy={busy}
                    checkedAt={status.checked_at}
                    onSwitch={() => switchPreset(item)}
                    onChoose={(to) => chooseModel(item, to)}
                    onUseBuiltIn={() => restoreBuiltIn(item)}
                  />
                ))}
              </ul>
            </Section>
          )}

          {status.registry_updated && (
            <p className="muted">Retirement dates come from a list shipped with the app (updated {status.registry_updated}).</p>
          )}
        </>
      )}
      {notice && (
        <p role="status" data-testid="model-health-notice">
          {notice}
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      </div>
    </Section>
  )
}

function othersSummary(items: ModelStatusItem[]): string {
  const current = items.filter((i) => i.status === 'current').length
  const rest = items.length - current
  return [current ? `${current} current` : '', rest ? `${rest} not checked` : ''].filter(Boolean).join(' · ')
}

const rowKey = (i: ModelStatusItem) => `${i.kind}|${i.preset_id ?? i.where}|${i.engine}|${i.model}`

function ModelRow({ item, canAct, busy, checkedAt, onSwitch, onChoose, onUseBuiltIn }: {
  item: ModelStatusItem
  canAct: boolean
  busy: string | null
  checkedAt: string | null
  onSwitch: () => void
  onChoose: (to: string) => void
  onUseBuiltIn: () => void
}) {
  const href = item.severity >= 1 ? compareHref(item) : null
  const help = item.severity >= 1 ? kindHelp(item) : null
  const switchable = item.can_switch && item.preset_id != null && !!item.replacement
  const key = `switch:${item.preset_id}`
  const where = whereLabel(item)
  return (
    <li className={`model-row sev-${Math.max(0, Math.min(3, item.severity))}`}>
      <div className="model-row-head">
        <Badge tone={modelStatusTone(item.status)}>{modelStatusLabel(item.status)}</Badge>
        <strong className="model-where">{where}</strong>
        <code className="model-name">{item.model}</code>
      </div>
      <p className="model-message">{item.message}</p>
      {item.note && <p className="muted model-note">{item.note}</p>}
      {help && <p className="muted model-help">{help}</p>}
      {(href || switchable) && (
        <div className="model-actions">
          {href && (
            <ButtonLink href={href} variant="secondary" size="sm" aria-label={`Compare ${item.model} with ${item.replacement} in Benchmark Lab`}>
              Compare in Benchmark Lab
            </ButtonLink>
          )}
          {switchable && canAct && (
            <ConfirmButton
              name={`${where} to ${item.replacement}`}
              label={`Switch preset to ${item.replacement}…`}
              ariaLabel={`Switch ${where} to ${item.replacement}`}
              confirmLabel={`Confirm switch to ${item.replacement}`}
              verb="switch"
              tone="primary"
              busy={busy === key}
              disabled={busy !== null && busy !== key}
              onConfirm={onSwitch}
            />
          )}
        </div>
      )}
      {switchable && !canAct && <p className="muted model-help">Switching a preset is PC only.</p>}
      {canChooseModel(item) && (
        <ModelChooser item={item} canAct={canAct} busy={busy} checkedAt={checkedAt} onChoose={onChoose} onUseBuiltIn={onUseBuiltIn} />
      )}
    </li>
  )
}

function ModelChooser({ item, canAct, busy, checkedAt, onChoose, onUseBuiltIn }: {
  item: ModelStatusItem
  canAct: boolean
  busy: string | null
  checkedAt: string | null
  onChoose: (to: string) => void
  onUseBuiltIn: () => void
}) {
  const candidates = item.candidates ?? []
  const [picked, setPicked] = useState('')
  const to = candidates.includes(picked) ? picked : (candidates[0] ?? '')
  const key = `choose:${item.kind}|${item.key}`
  const selectId = `model-choose-${item.kind}-${item.key}`
  const override = overrideLine(item)
  const empty = noCandidatesLine(item, checkedAt)
  return (
    <div className="model-choose">
      {override && <p className="model-help" data-testid="model-override-line">{override}</p>}
      {!canAct ? (
        <p className="muted model-help">Choosing another model is PC only.</p>
      ) : (
        <div className="model-actions">
          {candidates.length > 0 ? (
            <>
              <label htmlFor={selectId}>Choose another model</label>
              <select id={selectId} value={to} disabled={busy !== null} onChange={(e) => setPicked(e.target.value)}>
                {candidates.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
              <ConfirmButton
                name={`${whereLabel(item)} to ${to}`}
                label={`Use ${to}…`}
                ariaLabel={`Use ${to} for ${whereLabel(item)}`}
                confirmLabel={`Confirm use ${to}`}
                verb="use"
                tone="primary"
                busy={busy === key}
                disabled={busy !== null && busy !== key}
                onConfirm={() => onChoose(to)}
              />
            </>
          ) : (
            empty && <p className="muted model-help">{empty}</p>
          )}
          {item.is_override && (
            <button type="button" className={buttonClass('secondary', 'sm')} disabled={busy !== null} onClick={onUseBuiltIn}>
              Use the built-in again
            </button>
          )}
        </div>
      )}
    </div>
  )
}
