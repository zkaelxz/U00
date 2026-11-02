/*
 * The stream's chat under the lines: each message as written with its
 * translation beneath it, newest first. A message with nothing to translate
 * (emotes, numbers, a repeat) shows as written.
 */
import { Field } from '../../components/Field'
import { Toggle } from '../../components/Toggle'
import { useLiveChat, type ChatView } from './useLiveChat'

export function ReadChatField({ checked, disabled, onChange }: { checked: boolean; disabled: boolean; onChange: (v: boolean) => void }) {
  return (
    <Field label="Read chat"
      help="YouTube links only. Reads the stream's live chat and translates it in a panel under the lines. Takes effect when you press Start.">
      <Toggle checked={checked} disabled={disabled} onChange={onChange} />
    </Field>
  )
}

/** The panel for a session that reads chat; only mounted for one, so the chat is polled only then. */
export function LiveChatSection({ sessionId, running, language }: { sessionId: string; running: boolean; language: string }) {
  return <LiveChat view={useLiveChat(sessionId, running)} language={language} />
}

export function LiveChat({ view, language }: { view: ChatView; language: string }) {
  const shown = [...view.messages].reverse()
  return (
    <section className="card live-chat" aria-labelledby="live-chat-title" data-testid="live-chat">
      <div className="card-head">
        <h2 className="card-title" id="live-chat-title">Chat</h2>
      </div>
      {view.message && <p className="muted" role="status" data-testid="live-chat-status">{view.message}</p>}
      {!shown.length && view.state === 'reading' && <p className="muted">No chat messages yet.</p>}
      {shown.length > 0 && (
        <ol className="live-chat-list" aria-label="Chat messages, newest first">
          {shown.map((m) => (
            <li key={m.id}>
              <span className="live-chat-author">{m.author}{m.paid && <span className="live-chat-paid"> {m.paid}</span>}</span>
              <span className="live-src" lang={language}>{m.text}</span>
              {m.state === 'done' && <span className="live-en">{m.translation}</span>}
              {m.state === 'pending' && <span className="muted">Translating…</span>}
              {m.state === 'failed' && <span className="muted">Not translated.</span>}
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
