/*
 * "Story tools": who is, explain, recap, relationship map, the universe
 * wiki and Q&A. Each AI call is synchronous on the server and may answer
 * 429 (busy) or 403 (paid engine); each shows its own error line. With
 * Spoiler-free on, calls send up_to_line_idx = the last line of this page.
 */
import { useEffect, useRef, useState, type FormEvent } from 'react'

import { readerApi, wikiMarkdownUrl } from '../../api/reader'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { Section } from '../../components/Section'
import type {
  ReaderChatTurn,
  ReaderRecap,
  ReaderRelationshipMap,
  ReaderWikiList,
} from '../../types/reader'
import type { TranslateEngine } from '../../types/translate'
import { ActionError } from './ReaderAction'
import { useAction } from './useReaderAction'
import { EnginePicker } from './ReaderEngine'
import { trimHistory } from './readerPrefs'

type Common = {
  dramaId: number
  page: number
  chapterSize: number
  boundary: number | undefined
  engines: TranslateEngine[]
  engine: string
  onEngine: (name: string) => void
}

const scope = (boundary: number | undefined) => (boundary === undefined ? {} : { up_to_line_idx: boundary })

function Answer({ text }: { text: string | null | undefined }) {
  if (text === undefined) return null
  return (
    <p className="result reader-answer" role="status">
      {text || 'No answer came back. Try rewording it.'}
    </p>
  )
}

function WhoExplain({ dramaId, boundary, engine, paid }: { dramaId: number; boundary: number | undefined; engine: string; paid: boolean }) {
  const [name, setName] = useState('')
  const [phrase, setPhrase] = useState('')
  const [who, setWho] = useState<string | null | undefined>(undefined)
  const [explained, setExplained] = useState<string | null | undefined>(undefined)
  const whoAct = useAction(async () => {
    setWho((await readerApi.who(dramaId, { name: name.trim(), engine, ...scope(boundary) })).answer)
  })
  const explainAct = useAction(async () => {
    setExplained((await readerApi.explain(dramaId, { phrase: phrase.trim(), engine, ...scope(boundary) })).answer)
  })
  const submit = (fn: () => void) => (e: FormEvent) => {
    e.preventDefault()
    fn()
  }
  return (
    <>
      <form className="reader-ask-row" onSubmit={submit(() => void whoAct.run())}>
        <Field label="Who is…">
          <input value={name} maxLength={200} placeholder="A character's name" onChange={(e) => setName(e.target.value)} />
        </Field>
        <button type="submit" disabled={!name.trim() || !engine || whoAct.busy}>
          {whoAct.busy ? 'Asking…' : 'Ask'}
        </button>
      </form>
      <ActionError action={whoAct} paidEngine={paid} />
      <Answer text={who} />
      <form className="reader-ask-row" onSubmit={submit(() => void explainAct.run())}>
        <Field label="Explain">
          <input value={phrase} maxLength={200} placeholder="A phrase or reference" onChange={(e) => setPhrase(e.target.value)} />
        </Field>
        <button type="submit" disabled={!phrase.trim() || !engine || explainAct.busy}>
          {explainAct.busy ? 'Explaining…' : 'Explain'}
        </button>
      </form>
      <ActionError action={explainAct} paidEngine={paid} />
      <Answer text={explained} />
    </>
  )
}

function RecapAndMap({ dramaId, page, chapterSize, boundary, engine, paid }: Omit<Common, 'engines' | 'onEngine'> & { paid: boolean }) {
  const [recap, setRecap] = useState<ReaderRecap | null>(null)
  const [map, setMap] = useState<ReaderRelationshipMap | null>(null)
  const recapAct = useAction(async () => {
    setRecap(await readerApi.recap(dramaId, { page, chapter_size: chapterSize, engine }))
  })
  const mapAct = useAction(async () => {
    setMap(await readerApi.relationships(dramaId, { engine, ...scope(boundary) }))
  })
  return (
    <>
      <div className="actions">
        <button type="button" disabled={!engine || recapAct.busy || page <= 1} onClick={() => void recapAct.run()}>
          {recapAct.busy ? 'Summarising…' : 'Recap before this page'}
        </button>
        <button type="button" disabled={!engine || mapAct.busy} onClick={() => void mapAct.run()}>
          {mapAct.busy ? 'Mapping…' : 'Map relationships'}
        </button>
      </div>
      {page <= 1 && <p className="muted">Recap needs a page before this one.</p>}
      <ActionError action={recapAct} paidEngine={paid} />
      {recap && (
        <>
          <Answer text={recap.summary} />
          {recap.truncated && <p className="muted">Summary covers the last 400 lines.</p>}
        </>
      )}
      <ActionError action={mapAct} paidEngine={paid} />
      {map && (
        <div className="reader-map">
          {map.characters.length === 0 ? (
            <p className="muted">No characters found yet.</p>
          ) : (
            <>
              <h4>Characters</h4>
              <ul>
                {map.characters.map((c, i) => (
                  <li key={`${String(c.name)}-${i}`}>
                    <strong>{String(c.name ?? '')}</strong>
                    {c.role ? <span className="muted"> · {String(c.role)}</span> : null}
                    {c.description ? <div>{String(c.description)}</div> : null}
                  </li>
                ))}
              </ul>
              {map.relationships.length > 0 && (
                <>
                  <h4>Relationships</h4>
                  <ul>
                    {map.relationships.map((r, i) => (
                      <li key={i}>
                        {String(r.from ?? '')} → {String(r.to ?? '')}: {String(r.relation ?? '')}
                        {r.note ? <span className="muted"> ({String(r.note)})</span> : null}
                      </li>
                    ))}
                  </ul>
                </>
              )}
              {map.mermaid && (
                <details className="reader-mermaid">
                  <summary>Diagram text (Mermaid)</summary>
                  <pre>{map.mermaid}</pre>
                </details>
              )}
            </>
          )}
        </div>
      )}
    </>
  )
}

const CLEAR_TIMEOUT_MS = 5000

function WikiSection({ dramaId, boundary, engine, paid }: { dramaId: number; boundary: number | undefined; engine: string; paid: boolean }) {
  const [wiki, setWiki] = useState<ReaderWikiList | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [type, setType] = useState('')
  const [progress, setProgress] = useState<string | null>(null)
  const [armed, setArmed] = useState(false)
  const stop = useRef(false)
  // Leaving the page stops the update loop after its current batch.
  useEffect(() => () => {
    stop.current = true
  }, [])

  const params = { ...scope(boundary), ...(type ? { entry_type: type } : {}) }
  const key = JSON.stringify(params)
  const [reloadTick, setReloadTick] = useState(0)
  useEffect(() => {
    let live = true
    readerApi.wiki(dramaId, JSON.parse(key)).then(
      (w) => {
        if (!live) return
        setWiki(w)
        setLoadError(null)
      },
      (e: unknown) => {
        if (live) setLoadError(e)
      },
    )
    return () => {
      live = false
    }
  }, [dramaId, key, reloadTick])

  useEffect(() => {
    if (!armed) return
    const t = setTimeout(() => setArmed(false), CLEAR_TIMEOUT_MS)
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setArmed(false)
    }
    document.addEventListener('keydown', onKey)
    return () => {
      clearTimeout(t)
      document.removeEventListener('keydown', onKey)
    }
  }, [armed])

  // Resumable: each call does one bounded batch; call again from
  // next_line_idx while lines remain. resumeAt holds where to continue
  // after a failure (e.g. 429) or Stop.
  const [resumeAt, setResumeAt] = useState<number | null>(null)
  const update = useAction(async (from: number) => {
    stop.current = false
    let next: number | null = from
    let total = 0
    try {
      while (next !== null) {
        setResumeAt(next)
        const r = await readerApi.updateWiki(dramaId, { engine, from_line_idx: next, ...scope(boundary) })
        total += r.updated
        next = r.remaining > 0 ? r.next_line_idx : null
        if (next === null) {
          setResumeAt(null)
          setProgress(`Done. Updated ${total} entries.`)
        } else if (stop.current) {
          setResumeAt(next)
          setProgress(`Stopped. Updated ${total} entries; ${r.remaining} lines left.`)
          break
        } else {
          setProgress(`Updated ${total} so far · ${r.remaining} lines left…`)
        }
      }
    } finally {
      setReloadTick((n) => n + 1)
    }
  })
  const clear = useAction(async () => {
    await readerApi.clearWiki(dramaId)
    setArmed(false)
    setProgress(null)
    setReloadTick((n) => n + 1)
  })

  const entries = wiki?.entries ?? []
  return (
    <Section
      title="Universe wiki"
      storageKey="reader.wiki"
      count={entries.length > 0 ? entries.length : undefined}
      summary="People, places and things so far"
    >
      <ErrorBanner error={loadError} />
      <div className="actions">
        <button type="button" disabled={!engine || update.busy} onClick={() => void update.run(0)}>
          {update.busy ? 'Updating…' : 'Update wiki'}
        </button>
        {update.busy && (
          <button type="button" className="link" onClick={() => (stop.current = true)}>
            Stop
          </button>
        )}
        {!update.busy && resumeAt !== null && (
          <button type="button" onClick={() => void update.run(resumeAt ?? 0)}>
            Continue updating
          </button>
        )}
      </div>
      {progress && <p className="muted" role="status">{progress}</p>}
      {/* After a 429 part-way through, "Try again" continues from where it stopped. */}
      <ActionError action={{ ...update, retry: () => void update.run(resumeAt ?? 0) }} paidEngine={paid} />
      {wiki && wiki.entry_types.length > 1 && (entries.length > 0 || type !== '') && (
        <Field label="Show">
          <select value={type} onChange={(e) => setType(e.target.value)}>
            <option value="">Everything</option>
            {wiki.entry_types.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </Field>
      )}
      {wiki && entries.length === 0 && <p className="muted">No wiki entries yet. Update the wiki to build it.</p>}
      {entries.length > 0 && (
        <ul className="reader-wiki">
          {entries.map((e, i) => (
            <li key={e.id ?? i}>
              <strong>{e.name}</strong>
              {e.entry_type && <span className="badge">{e.entry_type}</span>}
              {e.description && <div>{e.description}</div>}
            </li>
          ))}
        </ul>
      )}
      {entries.length > 0 && (
        <div className="actions">
          <a href={wikiMarkdownUrl(dramaId, params)} download>Export .md</a>
          {armed ? (
            <>
              <button type="button" className="danger" disabled={clear.busy || update.busy} onClick={() => void clear.run()} autoFocus>
                {clear.busy ? 'Clearing…' : 'Confirm clear wiki'}
              </button>
              <button type="button" className="link" onClick={() => setArmed(false)}>Cancel</button>
              <span className="muted" aria-live="polite">Press again to delete every wiki entry for this drama.</span>
            </>
          ) : (
            <button type="button" className="danger" disabled={update.busy} onClick={() => setArmed(true)}>Clear wiki…</button>
          )}
        </div>
      )}
      <ActionError action={clear} />
    </Section>
  )
}

function AskSection({ dramaId, engine, paid }: { dramaId: number; engine: string; paid: boolean }) {
  const [history, setHistory] = useState<ReaderChatTurn[]>([])
  const [question, setQuestion] = useState('')
  const ask = useAction(async (q: string) => {
    const r = await readerApi.ask(dramaId, { question: q, chat_history: trimHistory(history), engine })
    setHistory((h) => trimHistory([...h, { role: 'user', content: q }, { role: 'assistant', content: r.answer ?? '' }]))
    setQuestion('')
  })
  const latest = [...history].reverse().find((t) => t.role === 'assistant')
  return (
    <Section title="Ask about the story" storageKey="reader.ask" summary="Questions answered from the drama's own lines">
      <p className="muted">Q&amp;A uses the whole drama, including later lines.</p>
      {history.length > 0 && (
        <ol className="reader-chat" aria-label="Conversation">
          {history.map((t, i) => (
            <li key={i} className={t.role === 'user' ? 'reader-chat-user' : 'reader-chat-ai'}>
              <span className="muted">{t.role === 'user' ? 'You' : 'AI'}</span>
              <div>{t.content || 'No answer came back.'}</div>
            </li>
          ))}
        </ol>
      )}
      <p className="sr-only" aria-live="polite">{latest ? latest.content || 'No answer came back.' : ''}</p>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault()
          if (question.trim()) void ask.run(question.trim())
        }}
      >
        <Field label="Question">
          <textarea rows={2} maxLength={2000} value={question} onChange={(e) => setQuestion(e.target.value)} />
        </Field>
        <div className="actions">
          <button type="submit" disabled={!question.trim() || !engine || ask.busy}>
            {ask.busy ? 'Asking…' : 'Ask'}
          </button>
          {history.length > 0 && (
            <button type="button" className="link" onClick={() => setHistory([])}>Clear conversation</button>
          )}
        </div>
      </form>
      <ActionError action={ask} paidEngine={paid} />
    </Section>
  )
}

export function StorySection(p: Common & { spoilerFree: boolean }) {
  const paid = !p.engines.find((e) => e.name === p.engine)?.free
  return (
    <>
      <Section title="Story tools" storageKey="reader.story" summary={p.spoilerFree ? 'Spoiler-free' : 'Spoiler-free off'}>
        <EnginePicker engines={p.engines} engine={p.engine} onChange={p.onEngine} />
        {!p.spoilerFree && (
          <p className="muted">Spoiler-free is off: Who is, Explain, relationships and the wiki may use later lines.</p>
        )}
        <WhoExplain dramaId={p.dramaId} boundary={p.boundary} engine={p.engine} paid={paid} />
        <RecapAndMap
          dramaId={p.dramaId}
          page={p.page}
          chapterSize={p.chapterSize}
          boundary={p.boundary}
          engine={p.engine}
          paid={paid}
        />
      </Section>
      <WikiSection dramaId={p.dramaId} boundary={p.boundary} engine={p.engine} paid={paid} />
      <AskSection dramaId={p.dramaId} engine={p.engine} paid={paid} />
    </>
  )
}
