// The chat: question box, Ask, and the answers so far. Each answer is plain
// text (never HTML), with the tier and engine that gave it, the tools it
// used, any proposed patch (not applied) and backlog suggestions the viewer
// can add by hand. After each answer the user may ask the next tier (after
// confirming exactly what is sent) or prepare a report for a developer.
import { useEffect, useRef, useState } from 'react'

import { MAX_QUESTION, askAssistant, prepareDeveloperReport, saveAssistantSettings, type Escalation } from '../../api/assistant'
import { Badge } from '../../components/Badge'
import { Card } from '../../components/Card'
import { Field } from '../../components/Field'
import { humanize } from '../../components/labels'
import { Section } from '../../components/Section'
import { Sheet } from '../../components/Sheet'
import { buttonClass } from '../../components/uiClasses'
import type { AskResponse, AssistantSettings, BacklogItem, BacklogKind, ChatTurn, GithubStatus, SuggestedBacklogItem } from '../../types/assistant'
import { CloudConsent } from './CloudConsent'
import { CopyButton } from './CopyButton'
import { DeliverPr } from './DeliverPr'
import { ReviewRolesSection } from './ReviewRolesSection'
import { verdictInfo } from './reviewFormat'
import {
  MODE_OFF_TEXT,
  PATCH_LABEL,
  assistantErrorText,
  compactArgs,
  engineChanges,
  historyOf,
  kindLabel,
  type Exchange,
} from './assistantFormat'
import {
  engineName,
  escalationPlan,
  failureText,
  nextTierOf,
  privacyNote,
  reportRequest,
  sendSummary,
  tierFailureOf,
  tierLabel,
  type EscalationPlan,
} from './escalation'

type AddToBacklog = (kind: BacklogKind, text: string) => Promise<BacklogItem>

type Props = {
  settings: AssistantSettings
  engine: string
  model: string
  onEngine: (engine: string) => void
  onModel: (model: string) => void
  onSettings: (s: AssistantSettings) => void
  onModeOff: () => void
  onAddToBacklog: AddToBacklog
  // GitHub delivery status (null: not loaded / PC-only).
  github?: GithubStatus | null
}

export function ChatCard({ settings, engine, model, onEngine, onModel, onSettings, onModeOff, onAddToBacklog, github = null }: Props) {
  const [exchanges, setExchanges] = useState<Exchange[]>([])
  const [question, setQuestion] = useState('')
  const [asking, setAsking] = useState(false)
  // The escalation the user is being asked to confirm, and the open developer report.
  const [offer, setOffer] = useState<Offer | null>(null)
  const [reportFor, setReportFor] = useState<number | null>(null)
  const nextId = useRef(1)

  const send = (q: string, history: ChatTurn[], eng: string, escalation?: Escalation) => {
    const id = nextId.current++
    setExchanges((xs) => [...xs, { id, question: q, response: null, error: null }])
    setAsking(true)
    // A typed model name belongs to the picked engine, not to another tier.
    askAssistant(q, history, eng, eng === engine ? model : '', undefined, escalation).then(
      (response) => {
        setExchanges((xs) => xs.map((x) => (x.id === id ? { ...x, response } : x)))
        setAsking(false)
      },
      (e: unknown) => {
        const failure = tierFailureOf(e)
        const text = failure ? failureText(failure) : assistantErrorText(e)
        setExchanges((xs) => xs.map((x) => (x.id === id ? { ...x, error: text, failure } : x)))
        setAsking(false)
        if (!failure && text === MODE_OFF_TEXT) onModeOff()
      },
    )
  }

  const ask = () => {
    const q = question.trim()
    if (!q || asking) return
    setQuestion('')
    send(q, historyOf(exchanges), engine)
  }

  const escalate = (index: number, next: string) => {
    const x = exchanges[index]
    setOffer({ plan: escalationPlan(exchanges, index), engine: next, from: x.response?.engine ?? x.failure?.engine ?? null })
  }

  return (
    <Card
      title="Ask"
      meta="Questions about this app's code, logs and settings. Answers can take a minute."
      aria-label="Ask the assistant"
      actions={
        <button
          type="button"
          className={buttonClass('ghost', 'sm')}
          disabled={asking || exchanges.length === 0}
          onClick={() => {
            setExchanges([])
            setOffer(null)
          }}
        >
          New chat
        </button>
      }
    >
      {exchanges.length > 0 && (
        <ol className="assistant-chat" aria-label="Conversation" aria-busy={asking || undefined}>
          {exchanges.map((x, i) => (
            <li key={x.id} className="assistant-turn">
              <p className="assistant-question">
                <span className="visually-hidden">You asked: </span>
                {x.question}
              </p>
              {x.response ? (
                <Answer response={x.response} question={x.question} github={github} onAddToBacklog={onAddToBacklog} />
              ) : x.error ? (
                <p className="error" role="alert">
                  {x.error}
                </p>
              ) : (
                <p className="muted" role="status">
                  Working on it. This can take a minute.
                </p>
              )}
              {(x.response || x.error) && !asking && (
                <TurnActions next={nextTierOf(x)} onEscalate={(next) => escalate(i, next)} onReport={() => setReportFor(i)} />
              )}
            </li>
          ))}
        </ol>
      )}
      <form
        className="assistant-ask"
        onSubmit={(e) => {
          e.preventDefault()
          ask()
        }}
      >
        <Field label="Question" help="Ctrl+Enter asks. The assistant sees the last 20 messages of this chat.">
          <textarea
            rows={3}
            maxLength={MAX_QUESTION}
            value={question}
            placeholder="e.g. Why does the Dub stage skip lines with no speaker?"
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                e.preventDefault()
                ask()
              }
            }}
          />
        </Field>
        <div className="assistant-actions">
          <button type="submit" className={buttonClass('primary')} disabled={asking || !question.trim()}>
            {asking ? 'Asking…' : 'Ask'}
          </button>
        </div>
      </form>
      <EngineSection
        settings={settings}
        engine={engine}
        model={model}
        onEngine={onEngine}
        onModel={onModel}
        onSettings={onSettings}
      />
      <LadderSection settings={settings} onSettings={onSettings} />
      <ReviewRolesSection settings={settings} implementEngine={engine} onSettings={onSettings} />
      {offer && (
        <EscalateDialog
          offer={offer}
          settings={settings}
          onSettings={onSettings}
          onClose={() => setOffer(null)}
          onSend={() => {
            const { plan, engine: next } = offer
            setOffer(null)
            send(plan.question, plan.history, next, { consent: true, evidence: plan.evidence })
          }}
        />
      )}
      {reportFor !== null && <ReportSheet exchanges={exchanges.slice(0, reportFor + 1)} onClose={() => setReportFor(null)} />}
    </Card>
  )
}

type Offer = { plan: EscalationPlan; engine: string; from: string | null }

function TurnActions({ next, onEscalate, onReport }: { next: string | null; onEscalate: (next: string) => void; onReport: () => void }) {
  return (
    <div className="assistant-actions" data-testid="assistant-turn-actions">
      {next && (
        <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => onEscalate(next)}>
          {`Ask a stronger model (${engineName(next)})`}
        </button>
      )}
      <button type="button" className={buttonClass('ghost', 'sm')} onClick={onReport}>
        Prepare a report for a developer
      </button>
    </div>
  )
}

type EscalateProps = {
  offer: Offer
  settings: AssistantSettings
  onSettings: (s: AssistantSettings) => void
  onClose: () => void
  onSend: () => void
}

function EscalateDialog({ offer, settings, onSettings, onClose, onSend }: EscalateProps) {
  const { plan, engine, from } = offer
  const tier = (settings.tiers ?? []).find((t) => t.engine === engine)
  const local = tier?.local ?? (settings.local_engines ?? []).includes(engine)
  // The server also refuses without the saved per-provider consent.
  const allowed = local || settings.cloud_consent?.[engine] === true
  const name = engineName(engine)
  return (
    <Sheet open title={`Ask ${name}?`} onClose={onClose}>
      <div className="assistant-sheet" data-testid="assistant-escalate">
        <p>{privacyNote(engine, local)}</p>
        <p>{local ? `${name} gets:` : `What will be sent to ${name}:`}</p>
        <ul>
          {sendSummary(plan, from).map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
        <p className="assistant-question">{plan.question}</p>
        {plan.evidence && (
          <details>
            <summary>Show the tool output that will be sent</summary>
            <pre className="assistant-pre">{plan.evidence}</pre>
          </details>
        )}
        {!allowed && (
          <>
            <p className="muted">{`First allow sending code and logs to ${name}. This is saved for ${name}; you can turn it off in the Engine section.`}</p>
            <CloudConsent settings={settings} onSettings={onSettings} only={[engine]} />
          </>
        )}
        <div className="assistant-actions">
          <button type="button" className={buttonClass('primary')} disabled={!allowed} onClick={onSend}>
            {local ? `Ask ${name}` : `Send to ${name}`}
          </button>
          <button type="button" className={buttonClass('ghost')} onClick={onClose}>
            Cancel
          </button>
        </div>
      </div>
    </Sheet>
  )
}

function ReportSheet({ exchanges, onClose }: { exchanges: Exchange[]; onClose: () => void }) {
  const [report, setReport] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Built once when the sheet opens, from the chat as it was then.
  const [request] = useState(() => reportRequest(exchanges))
  useEffect(() => {
    let live = true
    prepareDeveloperReport(request).then(
      (r) => live && setReport(r.report),
      (e: unknown) => live && setError(assistantErrorText(e)),
    )
    return () => {
      live = false
    }
  }, [request])
  return (
    <Sheet open title="Report for a developer" onClose={onClose}>
      <div className="assistant-sheet" data-testid="assistant-report">
        <p className="muted">
          This chat, what the tools found and the Diagnostics support report, with keys, tokens and this PC’s folder names removed. Nothing is uploaded: read it, copy it and send it to a developer yourself.
        </p>
        {report !== null ? (
          <>
            <pre className="assistant-pre assistant-report-text">{report}</pre>
            <CopyButton text={report} label="Copy report" />
          </>
        ) : error ? (
          <p className="error" role="alert">
            {error}
          </p>
        ) : (
          <p className="muted" role="status">
            Preparing the report…
          </p>
        )}
      </div>
    </Sheet>
  )
}

const TIER_SLOTS = 3

function orderOf(s: AssistantSettings): string[] {
  const order = s.tier_order ?? (s.tiers ?? []).map((t) => t.engine)
  return Array.from({ length: TIER_SLOTS }, (_, i) => order[i] ?? '')
}

function LadderSection({ settings, onSettings }: { settings: AssistantSettings; onSettings: (s: AssistantSettings) => void }) {
  const [slots, setSlots] = useState<string[]>(() => orderOf(settings))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const tiers = settings.tiers ?? []
  const summary = tiers.length ? tiers.map((t) => `${t.tier}. ${engineName(t.engine)}`).join(' → ') : 'None'
  const keys = settings.engine_keys ?? {}
  const picked = slots.filter(Boolean)
  const duplicate = new Set(picked).size !== picked.length

  const save = (order: string[] | null) => {
    setBusy(true)
    setError(null)
    saveAssistantSettings({ tiers: order }).then(
      (s) => {
        setBusy(false)
        setSlots(orderOf(s))
        onSettings(s)
      },
      (e: unknown) => {
        setBusy(false)
        setError(assistantErrorText(e))
      },
    )
  }

  return (
    <Section title="Escalation order" summary={summary} storageKey="assistant.tiers">
      <p className="muted">
        When an answer isn’t enough, you can ask the next tier, one step at a time and only when you choose. Keep tier 1 on Ollama so questions start on this PC. Engines with no key are skipped. After the last tier, prepare a report for a developer.
      </p>
      <ol className="assistant-tier-list" aria-label="Tiers in use">
        {tiers.map((t) => (
          <li key={t.engine}>{tierLabel(t.tier, t.engine, t.local)}</li>
        ))}
      </ol>
      <div className="assistant-engine">
        {slots.map((value, i) => (
          <Field key={i} label={`Tier ${i + 1}`}>
            <select value={value} onChange={(e) => setSlots((xs) => xs.map((x, j) => (j === i ? e.target.value : x)))}>
              <option value="">None</option>
              {settings.engine_choices.map((c) => (
                <option key={c} value={c}>
                  {keys[c] === false ? `${engineName(c)} (no key)` : engineName(c)}
                </option>
              ))}
            </select>
          </Field>
        ))}
      </div>
      <div className="assistant-actions">
        <button type="button" className={buttonClass('secondary', 'sm')} disabled={busy || duplicate || picked.length === 0} onClick={() => save(picked)}>
          {busy ? 'Saving…' : 'Save order'}
        </button>
        <button type="button" className={buttonClass('ghost', 'sm')} disabled={busy || !settings.tier_order} onClick={() => save(null)}>
          Use the default order
        </button>
        {duplicate && <span className="muted">Each engine can be one tier only.</span>}
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </Section>
  )
}

type AnswerProps = { response: AskResponse; question: string; github: GithubStatus | null; onAddToBacklog: AddToBacklog }

function Answer({ response, question, github, onAddToBacklog }: AnswerProps) {
  const calls = response.tool_calls ?? []
  return (
    <div className="assistant-response">
      <div className="assistant-answer" data-testid="assistant-answer">
        {response.answer}
      </div>
      <p className="assistant-by muted" data-testid="assistant-tier">
        {tierLabel(response.tier, response.engine, response.local)}
        {response.model ? ` · ${response.model}` : ''}
      </p>
      {calls.length > 0 && (
        <Section title={`Tools used (${calls.length})`}>
          <ul className="assistant-calls" aria-label="Tools used">
            {calls.map((c) => (
              <li key={c.id}>
                <div className="assistant-call-head">
                  <code className="assistant-call-name">{c.name}</code>
                  <Badge tone={c.ok ? 'ok' : 'bad'}>{c.ok ? 'OK' : 'Failed'}</Badge>
                </div>
                <code className="assistant-call-args">{compactArgs(c.args)}</code>
                {c.summary && <p className="muted assistant-call-summary">{c.summary}</p>}
              </li>
            ))}
          </ul>
        </Section>
      )}
      {(response.proposed_patches ?? []).map((p) => (
        <figure key={p.id} className="assistant-patch" aria-label="Proposed fix">
          <figcaption>
            <strong>{PATCH_LABEL}</strong>
            {p.files.length > 0 && <span className="muted"> Files: {p.files.join(', ')}</span>}
          </figcaption>
          <pre className="assistant-pre">{p.patch}</pre>
          <CopyButton text={p.patch} label="Copy proposed fix" />
          {/* Keyed by repo and base: changing either drops an open preview. */}
          <DeliverPr key={`${github?.repo ?? ''}|${github?.base_branch ?? ''}`} patch={p.patch} question={question} github={github} />
        </figure>
      ))}
      {response.review && <Review review={response.review} />}
      {(response.suggested_backlog ?? []).length > 0 && (
        <ul className="assistant-suggestions" aria-label="Suggested backlog items">
          {response.suggested_backlog.map((s, i) => (
            <Suggestion key={`${s.kind}-${i}`} item={s} onAdd={onAddToBacklog} />
          ))}
        </ul>
      )}
    </div>
  )
}

function Review({ review }: { review: NonNullable<AskResponse['review']> }) {
  const info = verdictInfo(review.verdict)
  const calls = review.tool_calls ?? []
  return (
    <section className="assistant-review" aria-label="Independent review" data-testid="assistant-review">
      <div className="assistant-call-head">
        <strong>Independent review</strong>
        <Badge tone={info.tone}>{info.label}</Badge>
        {review.engine && (
          <span className="muted">
            {humanize('engine', review.engine)}
            {review.model ? ` · ${review.model}` : ''}
          </span>
        )}
      </div>
      <p className="muted">{info.text}</p>
      {review.notes && <div className="assistant-answer">{review.notes}</div>}
      {calls.length > 0 && (
        <Section title={`Reviewer's tools (${calls.length})`}>
          <ul className="assistant-calls" aria-label="Reviewer's tools">
            {calls.map((c) => (
              <li key={c.id}>
                <div className="assistant-call-head">
                  <code className="assistant-call-name">{c.name}</code>
                  <Badge tone={c.ok ? 'ok' : 'bad'}>{c.ok ? 'OK' : 'Failed'}</Badge>
                </div>
                <code className="assistant-call-args">{compactArgs(c.args)}</code>
              </li>
            ))}
          </ul>
        </Section>
      )}
    </section>
  )
}

function Suggestion({ item, onAdd }: { item: SuggestedBacklogItem; onAdd: AddToBacklog }) {
  const [state, setState] = useState<'idle' | 'busy' | 'added'>('idle')
  const [error, setError] = useState<string | null>(null)
  const { label, tone } = kindLabel(item.kind)

  const add = () => {
    setState('busy')
    setError(null)
    onAdd(item.kind, item.text).then(
      () => setState('added'),
      (e: unknown) => {
        setState('idle')
        setError(assistantErrorText(e))
      },
    )
  }

  return (
    <li className="assistant-suggestion">
      <Badge tone={tone}>{label}</Badge>
      <span className="assistant-suggestion-text">{item.text}</span>
      <button
        type="button"
        className={buttonClass('secondary', 'sm')}
        disabled={state !== 'idle'}
        onClick={add}
      >
        {state === 'added' ? 'Added' : state === 'busy' ? 'Adding…' : 'Add to backlog'}
      </button>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </li>
  )
}

type EngineProps = Pick<Props, 'settings' | 'engine' | 'model' | 'onEngine' | 'onModel' | 'onSettings'>

function EngineSection({ settings, engine, model, onEngine, onModel, onSettings }: EngineProps) {
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const changes = engineChanges(settings, engine, model)
  const changed = Object.keys(changes).length > 0
  const defaultName = humanize('engine', settings.default_engine ?? 'ollama')
  const summary = `${engine ? humanize('engine', engine) : `Default (${defaultName})`}${model.trim() ? ` · ${model.trim()}` : ''}`
  const picked = engine || settings.engine || settings.default_engine || 'ollama'

  const save = () => {
    setBusy(true)
    setNote(null)
    setError(null)
    saveAssistantSettings(changes).then(
      (s) => {
        setBusy(false)
        setNote('Saved as the default.')
        onSettings(s)
      },
      (e: unknown) => {
        setBusy(false)
        setError(assistantErrorText(e))
      },
    )
  }

  return (
    <Section title="Engine" summary={summary} storageKey="assistant.engine">
      <div className="assistant-engine">
        <Field label="Engine" help="Which AI answers. The default, Ollama, runs on this PC; a cloud engine needs your OK to receive code and logs.">
          <select
            value={engine}
            onChange={(e) => {
              onEngine(e.target.value)
              setNote(null)
            }}
          >
            <option value="">{`Default (${defaultName})`}</option>
            {settings.engine_choices.map((c) => (
              <option key={c} value={c}>
                {humanize('engine', c)}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Model" help="Optional. Leave blank for the engine's default model.">
          <input
            type="text"
            value={model}
            spellCheck={false}
            autoComplete="off"
            placeholder="Engine default"
            onChange={(e) => {
              onModel(e.target.value)
              setNote(null)
            }}
          />
        </Field>
      </div>
      <div className="assistant-actions">
        <button type="button" className={buttonClass('secondary', 'sm')} disabled={busy || !changed} onClick={save}>
          {busy ? 'Saving…' : 'Save as default'}
        </button>
        <span className="muted" role="status">
          {note ?? ''}
        </span>
      </div>
      <CloudConsent settings={settings} onSettings={onSettings} only={[picked]} />
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </Section>
  )
}
