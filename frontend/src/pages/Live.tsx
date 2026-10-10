/*
 * Live page (#/live): paste a stream link (any public link yt-dlp can
 * resolve), pick the language and engine, Start; the transcript and its
 * translation arrive chunk by chunk (pushed over GET /api/events: a status
 * event says new lines exist and the page reads them from its own cursor;
 * polled every POLL_MS only while that stream is down). Stop ends it
 * and discards a chunk still in flight. Talks to /api/live
 * (services/live_service.py): each session has its own temp
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
  DEFAULT_FORM, DEFAULT_OPTIONS, FAST_CAPTIONS, LIVE_DEFAULT_ENGINE, LIVE_DEFAULT_MODEL, LIVE_LANGUAGES, MAX_MINUTES_RANGE, MAX_URL_LEN, OVERLAP_RANGE, POLL_MS, SEGMENT_RANGE, THINKING_SWITCH_ENGINES, WHISPER_SIZES,
  advancedSummary, buildStartBody, checkLiveUrl, checkOllama, describeLiveError, feedCues, fmtTs, getLive, hasPending, isActive,
  listLive, mergeCues, readFrom, pickEngine, pickSession, resolveModel, startLive, statusLine, stopLive, type LiveForm, type LiveOptions,
} from '../api/live'
import { engineShortName, translateApi, usableEngines } from '../api/translate'
import { Field } from '../components/Field'
import { ModelSelect } from '../components/ModelSelect'
import { Section } from '../components/Section'
import { Toggle } from '../components/Toggle'
import { useEventStream } from '../hooks/useEventStream'
import { usePcOnly } from '../hooks/usePcOnly'
import { usePersistedState } from '../hooks/usePersistedState'
import { LiveCaptions } from './live/LiveCaptions'
import { StreamEmbed } from './live/StreamEmbed'
import { DEFAULT_DELAY, DELAY_RANGE, canDelay, parseStreamUrl } from './live/embedLogic'
import type { LiveCue, LiveSessionStatus } from '../types/live'
import type { TranslateEngine } from '../types/translate'
import './live.css'
import { AI_ENGINE_LABEL } from '../helpText'
import { buttonClass } from '../components/uiClasses'

const numValue = (n: number) => (Number.isFinite(n) ? n : '')

type Session = { id: string; status: LiveSessionStatus | null; cues: LiveCue[]; next: number }

/** The translation, or why there is none yet: the transcript above it always stays. */
function TranslationText({ cue, waiting }: { cue: LiveCue; waiting: boolean }) {
  if (cue.translation === 'pending' && waiting) {
    return <span className="live-en muted" data-testid="live-translating">translating…</span>
  }
  if (cue.translation === 'pending' || cue.translation === 'cancelled') {
    return <span className="live-en muted" data-testid="live-untranslated">Not translated: the session stopped first.</span>
  }
  return <span className={cue.translation === 'failed' ? 'live-en warn' : 'live-en'}>{cue.translated}</span>
}

export default function LivePage() {
  const pc = usePcOnly()
  const [prefs, setPrefs] = usePersistedState<LiveOptions>('live.options', DEFAULT_OPTIONS)
  const [url, setUrl] = useState('')
  const [showVideo, setShowVideo] = usePersistedState<boolean>('live.showVideo', true)
  const [captionsOn, setCaptionsOn] = usePersistedState<boolean>('live.captions', true)
  const [theater, setTheater] = usePersistedState<boolean>('live.theater', false)
  const [videoDelay, setVideoDelay] = usePersistedState<number>('live.videoDelay', DEFAULT_DELAY)
  const form: LiveForm = { ...DEFAULT_FORM, ...prefs, url }
  const setOpt = <K extends keyof LiveOptions>(k: K, v: LiveOptions[K]) => setPrefs({ ...prefs, [k]: v })

  const [engines, setEngines] = useState<TranslateEngine[] | null>(null)
  const [enginesFailed, setEnginesFailed] = useState(false)
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
    translateApi.engines().then(setEngines, () => {
      setEngines([])
      setEnginesFailed(true)
    })
    listLive().then(
      (list) => {
        const id = pickSession(list)
        if (id && !sessionRef.current) setSession({ id, status: null, cues: [], next: 0 })
      },
      () => {},
    )
  }, [])

  const usable = engines ? usableEngines(engines) : []
  const engine = pickEngine(usable, form.engine)

  const canSwitchThinking = THINKING_SWITCH_ENGINES.includes(engine)
  const engineEntry = usable.find((e) => e.name === engine)
  const { model, fellBack } = resolveModel(engineEntry, form.model)
  const modelNote = enginesFailed
    ? "Couldn't load the model list; the engine's default model will be used."
    : fellBack ? "That model isn't offered any more; the engine's default model will be used." : null

  // Ollama is the default and runs on this PC: say plainly when it is not
  // there, instead of failing at Start. Nothing switches engine for the user.
  const [ollamaNote, setOllamaNote] = useState<string | null>(null)
  const [recheck, setRecheck] = useState(0)
  const checkModel = engine === 'ollama' ? model : null
  useEffect(() => {
    setOllamaNote(null)
    if (checkModel === null) return
    let alive = true
    checkOllama(checkModel).then(
      (r) => { if (alive) setOllamaNote(r.ok ? null : (r.message ?? 'Ollama is not ready.')) },
      () => {},
    )
    return () => { alive = false }
  }, [checkModel, recheck])

  // Poll the shown session: at once, then every POLL_MS while it is active.
  const sessionId = session?.id
  const poll = useCallback(async (id: string) => {
    const cur = sessionRef.current
    if (!cur || cur.id !== id) return false
    // From the oldest line still waiting for its translation, so it fills in.
    const after = readFrom(cur.cues, cur.next)
    try {
      const s = await getLive(id, after)
      // Merged by id: a reply that overlaps another changes nothing twice.
      setSession((prev) => {
        if (!prev || prev.id !== id) return prev
        return { id, status: s, cues: mergeCues(prev.cues, s.cues), next: Math.max(prev.next, s.next_index) }
      })
      if (sessionRef.current?.id === id) {
        sessionRef.current = { ...sessionRef.current, next: Math.max(sessionRef.current.next, s.next_index) }
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

  // Pushed status (cues: [] -- the text stays behind the GET): show it, and
  // read the new lines from our cursor when the session has more.
  const stream = useEventStream((type, data) => {
    const s = data as LiveSessionStatus | null
    const cur = sessionRef.current
    if (type !== 'live' || !s || !cur || s.session_id !== cur.id) return
    setSession((prev) => (prev && prev.id === s.session_id ? { ...prev, status: { ...s, cues: [] } } : prev))
    if (s.next_index > cur.next || hasPending(cur.cues)) void poll(cur.id)
  })
  const polling = stream.mode === 'poll'

  // One read at once and after every (re)connect; the timer only while the
  // stream is down.
  useEffect(() => {
    if (!sessionId) return
    let timer: ReturnType<typeof setTimeout> | undefined
    let alive = true
    const tick = async () => {
      const again = await poll(sessionId)
      if (alive && again && polling) timer = setTimeout(tick, POLL_MS)
    }
    void tick()
    return () => {
      alive = false
      clearTimeout(timer)
    }
  }, [sessionId, poll, polling, stream.syncs])

  const status = session?.status?.status ?? (session ? 'queued' : null)
  const active = !!session && isActive(status)
  const urlReason = checkLiveUrl(url)
  // While the engine list loads there is no engine to send; never start then
  // (a missing engine would fall back to the server default, a paid one).
  const blocker = urlReason ?? (engines === null
    ? 'Loading engines…'
    : !engine ? 'Still needed: an engine with a key (see Settings).' : null)

  async function start(e: React.FormEvent) {
    e.preventDefault()
    if (blocker || busy || active) return
    setBusy(true)
    setError(null)
    try {
      const { session_id } = await startLive(buildStartBody({ ...form, engine, model }))
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

  // The picture exists only while the session runs and Stop has not been
  // pressed: leaving it mounted would keep the stream's audio playing.
  const videoLive = !!session && status === 'running' && !stopping && url.trim() !== ''
  const streamRef = videoLive ? parseStreamUrl(url) : null
  const feed = session ? feedCues(session.cues) : []
  const total = session?.next ?? 0

  return (
    <section className={videoLive ? 'panel live-page live-wide' : 'panel page-narrow live-page'} aria-label="Live">
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
          <Field label={AI_ENGINE_LABEL} help="Engines without a key are hidden; add keys in Settings.">
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
          <ModelSelect engine={engineEntry} value={model} disabled={active} onChange={(m) => setOpt('model', m)}
            defaultModel={engine === LIVE_DEFAULT_ENGINE ? LIVE_DEFAULT_MODEL : undefined}
            help={engine === LIVE_DEFAULT_ENGINE
              ? 'Runs on this PC through Ollama. Pick another engine above to send the text to a hosted service instead.'
              : 'Engine default uses the model the engine runs on its own.'} />
        </div>
        {ollamaNote && (
          <p className="error" role="alert" data-testid="live-ollama-note">
            {ollamaNote} Pick another engine above, or fix Ollama and{' '}
            <button type="button" className="link" onClick={() => setRecheck((n) => n + 1)}>check again</button>.
          </p>
        )}
        {modelNote && <p className="muted" data-testid="live-model-note">{modelNote}</p>}
        <Section storageKey="live.advanced" title="Advanced" summary={advancedSummary(form)}>
          <div className="field-row">
            <button type="button" className="secondary" data-testid="live-fast-captions" disabled={active}
              onClick={() => setPrefs({ ...prefs, ...FAST_CAPTIONS })}>
              Fast captions
            </button>
            <p className="muted">Chunk 4 s, overlap 1 s, Whisper small, no thinking: lines show sooner, but Whisper hears less context, so wording is rougher. It replaces your current values for these four options, and this browser remembers them; to go back, set them by hand (the app's defaults are chunk 20 s, overlap 3 s, Whisper small, no thinking).</p>
          </div>
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
              <input type="number" min={SEGMENT_RANGE[0]} max={SEGMENT_RANGE[1]} step={1} value={numValue(form.segment_seconds)} disabled={active}
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
          <Field label="Reply without thinking"
            help="Faster lines: the translator answers straight away instead of reasoning first. Works with Ollama and DeepSeek.">
            <Toggle checked={form.reply_without_thinking && canSwitchThinking}
              disabled={active || !canSwitchThinking}
              onChange={(v) => setOpt('reply_without_thinking', v)} />
          </Field>
          {!canSwitchThinking && (
            <p className="muted" data-testid="live-thinking-note">
              {engineShortName({ name: engine })} can't switch thinking off from here, so this setting doesn't apply to it.
            </p>
          )}
        </Section>
        <div className="actions">
          {active ? (
            <button type="button" className={buttonClass('secondary')} onClick={() => void stop()} disabled={stopping}>
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
        <div className={videoLive ? (theater ? 'live-body has-video theater' : 'live-body has-video') : 'live-body'}>
        {videoLive && (
          <div className="card live-video" role="group" aria-label="Stream video">
            <div className="card-head">
              <h3 className="card-title">Video</h3>
              <div className="live-theater-field">
                <Field label="Larger video" help="Gives the video the full width and puts the lines under it.">
                  <Toggle checked={theater} onChange={setTheater} />
                </Field>
              </div>
              <Field label="Captions over video" help="Draws the current line over the picture, the translation once it is ready and the transcript until then.">
                <Toggle checked={captionsOn} onChange={setCaptionsOn} />
              </Field>
              <Field label="Show video" help="Plays the stream next to the lines. It stops when you stop the session or turn this off.">
                <Toggle checked={showVideo} onChange={setShowVideo} />
              </Field>
            </div>
            {!streamRef && <p className="muted">This site can't be shown here; the lines still work.</p>}
            {streamRef && showVideo && (
              <>
                <StreamEmbed stream={streamRef} delay={videoDelay}
                  captions={captionsOn ? (d) => <LiveCaptions cues={session.cues} delay={d} /> : undefined} />
                {canDelay(streamRef) && (
                  <Field label="Video delay" unit="s" help="The translation arrives several seconds after the speech. The picture plays this far behind live so they line up.">
                    <input type="range" min={DELAY_RANGE[0]} max={DELAY_RANGE[1]} step={1} value={videoDelay}
                      onChange={(e) => setVideoDelay(e.target.valueAsNumber)} />
                  </Field>
                )}
              </>
            )}
          </div>
        )}
        <div className="live-feed" aria-live="polite">
          <p className={status === 'error' ? 'error' : 'muted'} data-testid="live-status">
            {session.status ? statusLine(session.status, total) : 'Connecting…'}
          </p>
          {!!session.status?.notes?.length && (
            <ul className="muted live-notes" data-testid="live-notes" aria-label="Live events">
              {session.status.notes.map((n, i) => <li key={`${i}-${n}`}>{n}</li>)}
            </ul>
          )}
          {active && status === 'running' && !feed.length && <p className="muted">Waiting for the first chunk…</p>}
          {feed.length > 0 && (
            <ol className="live-cues" aria-label="Live lines, newest first">
              {feed.map((c) => (
                <li key={c.id}>
                  <span className="live-ts">{fmtTs(c.start)}</span>
                  <span className="live-src" lang={form.source_language}>{c.text}</span>
                  <TranslationText cue={c} waiting={active} />
                </li>
              ))}
            </ol>
          )}
          {total > feed.length && <p className="muted">Showing the newest {feed.length} of {total} lines.</p>}
        </div>
        </div>
      )}
    </section>
  )
}
