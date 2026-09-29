import { useEffect, useState } from 'react'

import { applyResearch, getResearchBudget, researchMetadata } from '../../../api/research'
import { Badge } from '../../../components/Badge'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { Section } from '../../../components/Section'
import { Toggle } from '../../../components/Toggle'
import { buttonClass } from '../../../components/uiClasses'
import type { ResearchBudget, ResearchChoice, ResearchMode, ResearchResult } from '../../../types/research'
import {
  budgetLine, choicesFor, confidenceLabel, costLine, defaultChoices, effectiveChoices, fieldLabel, hostOf,
} from '../researchForm'
import { useStage } from '../StageContext'
import './preamble.css'
import './research.css'

const MODES: [ResearchMode, string][] = [
  ['quick', 'Quick'],
  ['deep', 'Deep'],
  ['verify', 'Verify'],
]
const MODE_HELP: Record<ResearchMode, string> = {
  quick: 'A fast identity check: titles and main credits.',
  deep: 'More sources, fuller record.',
  verify: 'Checks the values already filled in.',
}
const MODEL_LABELS: Record<string, string> = {
  'gemini-flash-lite-latest': 'Gemini Flash-Lite',
  'gemini-flash-latest': 'Gemini Flash',
}

function Choice({ name, label, value, options, onChange }: {
  name: string; label: string; value: ResearchChoice
  options: { value: ResearchChoice; label: string }[]; onChange: (v: ResearchChoice) => void
}) {
  return (
    <fieldset className="segmented research-choice">
      <legend className="visually-hidden">{label}</legend>
      {options.map((o) => (
        <label key={o.value}>
          <input type="radio" name={name} value={o.value} checked={value === o.value} onChange={() => onChange(o.value)} />
          <span>{o.label}</span>
        </label>
      ))}
    </fieldset>
  )
}

// "Research online" (roadmap Step 37): Gemini with Google Search grounding.
// A lookup writes nothing; each field shows its own sources, and a value that
// differs from the saved one is only written if the user picks Replace.
export function ResearchPanel() {
  const { dramaId, refetchDrama } = useStage()
  const [budget, setBudget] = useState<ResearchBudget | null>(null)
  const [mode, setMode] = useState<ResearchMode>('quick')
  const [model, setModel] = useState('gemini-flash-lite-latest')
  const [allowPaid, setAllowPaid] = useState(false)
  const [result, setResult] = useState<ResearchResult | null>(null)
  const [choices, setChoices] = useState<Record<string, ResearchChoice>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [notice, setNotice] = useState<string | null>(null)

  useEffect(() => {
    getResearchBudget().then(setBudget, () => setBudget(null))
  }, [])

  const run = () => {
    setBusy(true)
    setNotice(null)
    researchMetadata(dramaId, { mode, model, allow_paid: allowPaid }).then(
      (r) => {
        setError(null)
        setResult(r)
        setBudget(r.budget)
        setChoices(defaultChoices(r.fields))
        if (!r.fields.length) setNotice('The sources gave nothing usable.')
        setBusy(false)
      },
      (e: unknown) => {
        setError(e)
        setBusy(false)
      },
    )
  }

  const toApply = effectiveChoices(choices)
  const apply = () => {
    if (!result) return
    setBusy(true)
    applyResearch(dramaId, result.research_id, toApply).then(
      (r) => {
        setError(null)
        setResult(null)
        const parts = []
        if (r.replaced.length) parts.push(`${r.replaced.length} field(s) updated`)
        if (r.saved_alternates.length) parts.push(`${r.saved_alternates.length} kept beside the existing value`)
        setNotice(`${parts.join(', ')}. Sources saved with each field.`)
        setBusy(false)
        refetchDrama()
      },
      (e: unknown) => {
        setError(e)
        setBusy(false)
      },
    )
  }

  const needsPaid = !!budget && budget.free_remaining <= 0
  const blocked = !budget?.key_configured || (needsPaid && (!allowPaid || budget.free_tier_key))

  return (
    <section className="panel" aria-label="Research online">
      <Section
        storageKey="source.research"
        title="Research online"
        summary={budget ? budgetLine(budget) : 'Gemini search with cited sources'}
      >
        <div className="source-panel research-panel">
          <p className="muted">
            Looks the title up with Google Search through your Gemini key and shows where each value came from.
            Nothing is saved until you choose.
          </p>
          {budget && !budget.key_configured && (
            <p className="muted">Add a Gemini key in Settings to use this.</p>
          )}
          <Field label="Depth" help={MODE_HELP[mode]}>
            <select value={mode} onChange={(e) => setMode(e.target.value as ResearchMode)}>
              {MODES.map(([v, t]) => (
                <option key={v} value={v}>{t}</option>
              ))}
            </select>
          </Field>
          <Field label="Model" help="Flash-Lite is cheaper; Flash is stronger. The app never switches models on its own.">
            <select value={model} onChange={(e) => setModel(e.target.value)}>
              {(budget?.models ?? ['gemini-flash-lite-latest', 'gemini-flash-latest']).map((m) => (
                <option key={m} value={m}>{MODEL_LABELS[m] ?? m}</option>
              ))}
            </select>
          </Field>
          {needsPaid && !budget?.free_tier_key && (
            <Field label="Allow paid searches" help="Today's free searches are used up. A paid search costs about $0.035 plus tokens, and still respects your monthly cap.">
              <Toggle checked={allowPaid} onChange={setAllowPaid} />
            </Field>
          )}
          {budget && <p className="muted" data-testid="research-cost">{costLine(budget, allowPaid)}</p>}
          <div>
            <button type="button" className={buttonClass('primary')} disabled={busy || blocked} onClick={run}>
              {busy && !result ? 'Researching…' : 'Research online'}
            </button>
          </div>

          {result && result.fields.length > 0 && (
            <>
              <p className="muted">
                {result.cached ? 'From an earlier lookup (no search used).' : `Looked up ${new Date(result.retrieved_at).toLocaleString()}.`}
              </p>
              <ul className="research-rows" aria-label="Researched metadata">
                {result.fields.map((f) => {
                  const opts = choicesFor(f)
                  return (
                    <li key={f.field} className="research-row">
                      <div className="research-head">
                        <strong>{fieldLabel(f.field)}</strong>
                        <Badge tone={f.status === 'conflict' ? 'warn' : f.status === 'same' ? 'ok' : 'neutral'}>
                          {f.status === 'conflict' ? 'Differs' : f.status === 'same' ? 'Matches' : 'New'}
                        </Badge>
                        <span className="muted">{confidenceLabel(f.confidence)}</span>
                      </div>
                      <p className="research-value">{f.value}</p>
                      {f.status === 'conflict' && <p className="muted">Existing: {f.current}</p>}
                      <p className="research-sources">
                        {f.sources.length ? (
                          <>
                            Sources:{' '}
                            {f.sources.map((s, i) => (
                              <span key={s.url}>
                                {i > 0 && ', '}
                                <a href={s.url} target="_blank" rel="noopener noreferrer nofollow">{s.title || hostOf(s.url)}</a>
                              </span>
                            ))}
                          </>
                        ) : (
                          <span className="muted">No source cited for this value.</span>
                        )}
                      </p>
                      {opts.length > 0 && (
                        <Choice
                          name={`research-${f.field}`}
                          label={`${fieldLabel(f.field)}: what to do`}
                          value={choices[f.field] ?? 'keep'}
                          options={opts}
                          onChange={(v) => setChoices((c) => ({ ...c, [f.field]: v }))}
                        />
                      )}
                    </li>
                  )
                })}
              </ul>
              {result.related.length > 0 && (
                <div>
                  <p className="muted">Related works the sources mention (research them separately if you want):</p>
                  <ul aria-label="Related works">
                    {result.related.map((r) => (
                      <li key={r.title}>{r.title}{r.relation && <span className="muted"> · {r.relation}</span>}</li>
                    ))}
                  </ul>
                </div>
              )}
              <div className="source-file">
                <button type="button" className={buttonClass('primary')} disabled={busy || !Object.keys(toApply).length} onClick={apply}>
                  Apply choices
                </button>
                <button type="button" className={buttonClass('ghost')} onClick={() => setResult(null)}>
                  Discard
                </button>
              </div>
            </>
          )}
          {notice && <p role="status">{notice}</p>}
          <ErrorBanner error={error} onDismiss={() => setError(null)} />
        </div>
      </Section>
    </section>
  )
}
