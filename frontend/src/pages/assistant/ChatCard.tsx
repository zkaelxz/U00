// The chat: question box, Ask, and the answers so far. Each answer is plain
// text (never HTML), with the tools it used, any proposed patch (not applied)
// and backlog suggestions the viewer can add by hand.
import { useRef, useState } from 'react'

import { MAX_QUESTION, askAssistant, saveAssistantSettings } from '../../api/assistant'
import { Badge } from '../../components/Badge'
import { Card } from '../../components/Card'
import { Field } from '../../components/Field'
import { humanize } from '../../components/labels'
import { Section } from '../../components/Section'
import { buttonClass } from '../../components/uiClasses'
import type { AskResponse, AssistantSettings, BacklogItem, BacklogKind, SuggestedBacklogItem } from '../../types/assistant'
import { CopyButton } from './CopyButton'
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
}

export function ChatCard({ settings, engine, model, onEngine, onModel, onSettings, onModeOff, onAddToBacklog }: Props) {
  const [exchanges, setExchanges] = useState<Exchange[]>([])
  const [question, setQuestion] = useState('')
  const [asking, setAsking] = useState(false)
  const nextId = useRef(1)

  const ask = () => {
    const q = question.trim()
    if (!q || asking) return
    const id = nextId.current++
    const history = historyOf(exchanges)
    setExchanges((xs) => [...xs, { id, question: q, response: null, error: null }])
    setQuestion('')
    setAsking(true)
    askAssistant(q, history, engine, model).then(
      (response) => {
        setExchanges((xs) => xs.map((x) => (x.id === id ? { ...x, response } : x)))
        setAsking(false)
      },
      (e: unknown) => {
        const text = assistantErrorText(e)
        setExchanges((xs) => xs.map((x) => (x.id === id ? { ...x, error: text } : x)))
        setAsking(false)
        if (text === MODE_OFF_TEXT) onModeOff()
      },
    )
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
          onClick={() => setExchanges([])}
        >
          New chat
        </button>
      }
    >
      {exchanges.length > 0 && (
        <ol className="assistant-chat" aria-label="Conversation" aria-busy={asking || undefined}>
          {exchanges.map((x) => (
            <li key={x.id} className="assistant-turn">
              <p className="assistant-question">
                <span className="visually-hidden">You asked: </span>
                {x.question}
              </p>
              {x.response ? (
                <Answer response={x.response} onAddToBacklog={onAddToBacklog} />
              ) : x.error ? (
                <p className="error" role="alert">
                  {x.error}
                </p>
              ) : (
                <p className="muted" role="status">
                  Working on it. This can take a minute.
                </p>
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
    </Card>
  )
}

function Answer({ response, onAddToBacklog }: { response: AskResponse; onAddToBacklog: AddToBacklog }) {
  const calls = response.tool_calls ?? []
  return (
    <div className="assistant-response">
      <div className="assistant-answer" data-testid="assistant-answer">
        {response.answer}
      </div>
      <p className="assistant-by muted">
        {humanize('engine', response.engine)}
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
        </figure>
      ))}
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
  const summary = `${engine ? humanize('engine', engine) : 'Server default'}${model.trim() ? ` · ${model.trim()}` : ''}`

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
        <Field label="Engine" help="Which AI answers. Server default uses the app's usual engine.">
          <select
            value={engine}
            onChange={(e) => {
              onEngine(e.target.value)
              setNote(null)
            }}
          >
            <option value="">Server default</option>
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
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </Section>
  )
}
