/*
 * Live page (#/live): paste a stream link (any public link yt-dlp can
 * resolve), pick the language and engine, Start; the transcript and its
 * translation arrive chunk by chunk (polled every POLL_MS). Stop ends it
 * and discards a chunk still in flight. Ports tabs/live_tab.py (LV01-LV06)
 * over /api/live (services/live_service.py): each session has its own temp
 * folder, Use GPU reaches Whisper, and "Stop after" is a hard cap.
 *
 * One session at a time (the API answers 409 to a second). On load the
 * page shows the running session, else the latest one, so a reload or a
 * second device sees the same feed. Starting needs `media.import_url`
 * (and `engines.paid` for a paid engine); from another device a 403 is
 * explained instead of the generic text. Browser cookies never travel over
 * the API, so a stream that needs a signed-in browser won't resolve here.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError } from '../api/client'
import {
  DEFAULT_FORM, DEFAULT_OPTIONS, LIVE_LANGUAGES, MAX_MINUTES_RANGE, MAX_URL_LEN, OVERLAP_RANGE, POLL_MS, SEGMENT_RANGE, WHISPER_SIZES,
  advancedSummary, appendCues, buildStartBody, checkLiveUrl, describeLiveError, feedCues, fmtTs, getLive, isActive,
  listLive, pickSession, startLive, statusLine, stopLive, type LiveForm, type LiveOptions,
} from '../api/live'
import { engineShortName, translateApi, usableEngines } from '../api/translate'
import { Field } from '../components/Field'
import { Section } from '../components/Section'
import { usePcOnly } from '../hooks/usePcOnly'
import { usePersistedState } from '../hooks/usePersistedState'
import type { LiveCue, LiveSessionStatus } from '../types/live'
import type { TranslateEngine } from '../types/translate'
import './live.css'

const numValue = (n: number) => (Number.isFinite(n) ? n : '')

type Session = { id: string; status: LiveSessionStatus | null; cues: LiveCue[]; next: number }

export default function LivePage() {
  const pc = usePcOnly()
  const [prefs, setPrefs] = usePersistedState<LiveOptions>('live.options', DEFAULT_OPTIONS)
  const [url, setUrl] = useState('')
  const form: LiveForm = { ...DEFAULT_FORM, ...prefs, url }
  const setOpt = <K extends keyof LiveOptions>(k: K, v: LiveOptions[K]) => setPrefs({ ...prefs, [k]: v })

  const [engines, setEngines] = useState<TranslateEngine[] | null>(null)
  const [session, setSession] = useState<Session | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [stopping, setStopping] = useState(false)
  const sessionRef = useRef<Session | null>(null)
  // Declared before the polling effect so a new session is in the ref first.
  useEffect(() => {
    sessionRef.current = session
  }, [session])

  useEffect(() => {
    translateApi.engines().then(setEngines, () => setEngines([]))
    listLive().then(
      (list) => {
        const id = pickSession(list)
        if (id && !sessionRef.current) setSession({ id, status: null, cues: [], next: 0 })
      },
      () => {},
    )
  }, [])

  const usable = engines ? usableEngines(engines) : []
  const engine = usable.some((e) => e.name === form.engine) ? form.engine : (usable[0]?.name ?? '')

  // Poll the shown session: at once, then every POLL_MS while it is active.
  const sessionId = session?.id
  const poll = useCallback(async (id: string) => {
    const cur = sessionRef.current
    if (!cur || cur.id !== id) return false
    const after = cur.next
    try {
      const s = await getLive(id, after)
      // Two polls can overlap (Stop polls at once): only the one that asked
      // from the current index appends, so no line is shown twice.
      setSession((prev) => {
        if (!prev || prev.id !== id) return prev
        if (prev.next !== after) return { ...prev, status: s }
        return { id, status: s, cues: appendCues(prev.cues, s.cues), next: s.next_index }
      })
      if (sessionRef.current?.id === id && sessionRef.current.next === after) {
        sessionRef.current = { ...sessionRef.current, next: s.next_index }
      }
      return isActive(s.status)
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setSession(null)
        setError('That live session is gone (Baihe was restarted). Start a new one.')
        return false
      }
      return true // a network blip: keep trying
    }
  }, [])

  useEffect(() => {
    if (!sessionId) return
    let timer: ReturnType<typeof setTimeout> | undefined
    let alive = true
    const tick = async () => {
      const again = await poll(sessionId)
      if (alive && again) timer = setTimeout(tick, POLL_MS)
    }
    void tick()
    return () => {
      alive = false
      clearTimeout(timer)
    }
  }, [sessionId, poll])

  const status = session?.status?.status ?? (session ? 'queued' : null)
  const active = !!session && isActive(status)
  const urlReason = checkLiveUrl(url)
  const blocker = urlReason ?? (engines && !engine ? 'Still needed: an engine with a key (see Settings).' : null)

  async function start(e: React.FormEvent) {
    e.preventDefault()
    if (blocker || busy || active) return
    setBusy(true)
    setError(null)
    try {
      const { session_id } = await startLive(buildStartBody({ ...form, engine }))
      setStopping(false)
      setSession({ id: session_id, status: null, cues: [], next: 0 })
    } catch (err) {
      setError(describeLiveError(err))
    } finally {
      setBusy(false)
    }
  }

  async function stop() {
    if (!session) return
    setStopping(true)
    setError(null)
    try {
      await stopLive(session.id)
      await poll(session.id)
    } catch (err) {
      setStopping(false)
      setError(describeLiveError(err))
    }
  }

  const feed = session ? feedCues(session.cues) : []
  const total = session?.next ?? 0

  return (
    <section className="panel page-narrow live-page" aria-label="Live">
      <h2>Live</h2>
      {/* noValidate: buildStartBody clamps the numbers, as the service does. */}
      <form onSubmit={start} className="live-form" noValidate>
        <Field label="Stream link" help="A live stream or video page (YouTube, Bilibili, …) that yt-dlp can open. Signed-in (cookie) streams don't work from here.">
          <input
            type="url"
            inputMode="url"
            autoComplete="off"
            spellCheck={false}
            maxLength={MAX_URL_LEN}
            placeholder="https://www.youtube.com/watch?v=…"
            value={url}
            disabled={active}
            onChange={(e) => setUrl(e.target.value)}
          />
        </Field>
        <div className="field-row">
          <Field label="Language">
            <select value={form.source_language} disabled={active} onChange={(e) => setOpt('source_language', e.target.value)}>
              {LIVE_LANGUAGES.map((l) => (
                <option key={l.code} value={l.code}>
                  {l.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Engine" help="Engines without a key are hidden; add keys in Settings.">
            <select value={engine} disabled={active || !usable.length} onChange={(e) => setOpt('engine', e.target.value)}>
              {!usable.length && <option value="">{engines ? 'No engine with a key' : 'Loading…'}</option>}
              {usable.map((en) => (
                <option key={en.name} value={en.name}>
                  {engineShortName(en)}
                  {en.free ? '' : ' (paid)'}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <Section storageKey="live.advanced" title="Advanced" summary={advancedSummary(form)}>
          <div className="field-row">
            <Field label="Whisper model" help="Smaller is faster per chunk, closer to real time; medium is usually too slow for short chunks.">
              <select value={form.whisper_size} disabled={active} onChange={(e) => setOpt('whisper_size', e.target.value)}>
                {WHISPER_SIZES.map((w) => (
                  <option key={w} value={w}>
                    {w}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Chunk" unit="s" help="Shorter shows lines sooner; longer transcribes better per chunk.">
              <input type="number" min={SEGMENT_RANGE[0]} max={SEGMENT_RANGE[1]} step={5} value={numValue(form.segment_seconds)} disabled={active}
                onChange={(e) => setOpt('segment_seconds', e.target.valueAsNumber)} />
            </Field>
            <Field label="Overlap" unit="s" help="Re-hears this much of the previous chunk so a sentence cut at a boundary is heard whole. 0 turns it off; at most half the chunk.">
              <input type="number" min={OVERLAP_RANGE[0]} max={OVERLAP_RANGE[1]} step={1} value={numValue(form.overlap_seconds)} disabled={active}
                onChange={(e) => setOpt('overlap_seconds', e.target.valueAsNumber)} />
            </Field>
            <Field label="Stop after" unit="min" help="A hard cap on how long the session runs (and spends).">
              <input type="number" min={MAX_MINUTES_RANGE[0]} max={MAX_MINUTES_RANGE[1]} step={1} value={numValue(form.max_minutes)} disabled={active}
                onChange={(e) => setOpt('max_minutes', e.target.valueAsNumber)} />
            </Field>
          </div>
          <label className="live-check">
            <input type="checkbox" checked={form.use_gpu} disabled={active} onChange={(e) => setOpt('use_gpu', e.target.checked)} />
            Use GPU for Whisper
          </label>
        </Section>
        <div className="actions">
          {active ? (
            <button type="button" className="primary" onClick={() => void stop()} disabled={stopping}>
              {stopping ? 'Stopping…' : status === 'queued' ? 'Cancel' : 'Stop'}
            </button>
          ) : (
            <button type="submit" className="primary" disabled={!!blocker || busy}>
              {busy ? 'Starting…' : 'Start'}
            </button>
          )}
        </div>
        {!active && blocker && <p className="muted">{blocker}</p>}
        {pc === 'remote' && !active && (
          <p className="muted">From this device, starting needs the owner to allow importing from links.</p>
        )}
      </form>
      {error && (
        <p className="error live-error" role="alert">
          {error}
        </p>
      )}

      {session && (
        <div className="live-feed" aria-live="polite">
          <p className={status === 'error' ? 'error' : 'muted'} data-testid="live-status">
            {session.status ? statusLine(session.status, total) : 'Connecting…'}
          </p>
          {active && status === 'running' && !feed.length && <p className="muted">Waiting for the first chunk…</p>}
          {feed.length > 0 && (
            <ol className="live-cues" aria-label="Live lines, newest first">
              {feed.map((c, i) => (
                <li key={total - i}>
                  <span className="live-ts">{fmtTs(c.start)}</span>
                  <span className="live-src" lang={form.source_language}>{c.text}</span>
                  <span className={c.translated.startsWith('[translation failed') ? 'live-en warn' : 'live-en'}>{c.translated}</span>
                </li>
              ))}
            </ol>
          )}
          {total > feed.length && <p className="muted">Showing the newest {feed.length} of {total} lines.</p>}
        </div>
      )}
    </section>
  )
}
