/*
 * Live page (#/live): paste a stream link (any public link yt-dlp can
 * resolve), pick the language and engine, Start; the transcript and its
 * translation arrive chunk by chunk (pushed over GET /api/events: a status
 * event says new lines exist and the page reads them from its own cursor;
 * polled every POLL_MS only while that stream is down). Stop captions the
 * audio already captured and then ends; Stop now ends at once and discards a
 * chunk still in flight (following the session: live/useLiveSession.ts). Talks to /api/live
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
import { useEffect, useId, useState } from 'react'

import { api, hasStatus } from '../api/client'
import {
  DEFAULT_FORM, LIVE_FORM_FALLBACK, FAST_CAPTIONS, BALANCED_CAPTIONS, WHISPER_HELP, LANGUAGE_MODEL_NOTE,
  advancedSummary, applyCaptionPreset, buildStartBody, captionPresetModel, checkLiveUrl, describeLiveError, feedCues, fmtTs, formDefaults, getLiveOptions, glossaryChoices, glossaryIds, glossaryValue,
  isActive, lagLine, liveEngineView, pickEngine, resolveLiveModel, startLive, statusLine, statusTone, type LiveForm,
} from '../api/live'
import { getAllSeries } from '../api/library'
import { engineShortName, translateApi, usableEngines } from '../api/translate'
import { Field } from '../components/Field'
import { HelpTip } from '../components/HelpTip'
import { SettingsMentions } from '../components/SettingLink'
import { PageShell } from '../components/PageShell'
import { ModelSelect } from '../components/ModelSelect'
import { Section } from '../components/Section'
import { Toggle } from '../components/Toggle'
import { useLivePrefs } from './live/useLivePrefs'
import { useHolds } from '../hooks/useHolds'
import { usePcOnly } from '../hooks/usePcOnly'
import { useReadiness } from '../hooks/useReadiness'
import { usePersistedState } from '../hooks/usePersistedState'
import { followOptions } from '../api/liveFollows'
import { useDeveloperMode } from './assistant/developerMode'
import { NeedsInstall } from './readiness/NeedsInstall'
import { liveBlockers, liveGpuReason, liveNeeds, liveWhisperOptionNote, liveWhisperSize } from './readiness/needs'
import { whisperExclusionReason } from './workspace/sourceForm'
import { FollowedChannels } from './live/FollowedChannels'
import { LiveCaptions } from './live/LiveCaptions'
import { SaveSession } from './live/SaveSession'
import { LiveChatSection, ReadChatField } from './live/LiveChat'
import { DelayedPlayer } from './live/DelayedPlayer'
import { useLiveSession } from './live/useLiveSession'
import { OllamaNote } from './live/OllamaNote'
import { useOllamaCheck } from './live/useOllamaCheck'
import { StreamEmbed } from './live/StreamEmbed'
import { DEFAULT_DELAY, DELAY_RANGE, autoDelay, canDelay, sessionStream, steadyDelay } from './live/embedLogic'
import type { LiveCue } from '../types/live'
import type { TranslateEngine } from '../types/translate'
import './live.css'
import { AI_ENGINE_LABEL } from '../helpText'
import { buttonClass } from '../components/uiClasses'
import { routeHref } from '../router'

const numValue = (n: number | null) => (n !== null && Number.isFinite(n) ? n : '')

/** "A and B", "A, B and C": engine names as the picker shows them. */
const listNames = (names: string[]) => {
  const shown = names.map((name) => engineShortName({ name }))
  return shown.length > 1 ? `${shown.slice(0, -1).join(', ')} and ${shown[shown.length - 1]}` : shown.join('')
}


/** The translation, or why there is none yet: the transcript above it always stays. */
function TranslationText({ cue, waiting }: { cue: LiveCue; waiting: boolean }) {
  if (cue.translation === 'pending' && waiting) {
    return <span className="live-en muted" data-testid="live-translating">translating…</span>
  }
  if (cue.translation === 'skipped') {
    return <span className="live-en muted" data-testid="live-untranslated">Not translated: skipped to keep up with the stream.</span>
  }
  if (cue.translation === 'pending' || cue.translation === 'cancelled') {
    return <span className="live-en muted" data-testid="live-untranslated">Not translated: the session stopped first.</span>
  }
  return <span className={cue.translation === 'failed' ? 'live-en warn' : 'live-en'}>{cue.translated}</span>
}

/** Kept out of LivePage so that component stays under the length ceiling in componentLength.test.ts. */
function CueFeed({ cues, lang, waiting }: { cues: LiveCue[]; lang: string; waiting: boolean }) {
  return (
    <ol className="live-cues" aria-label="Live lines, newest first">
      {cues.map((c) => (
        <li key={c.id}>
          <span className="live-ts">{fmtTs(c.start)}</span>
          <span className="live-src" lang={lang}>{c.text}</span>
          <TranslationText cue={c} waiting={waiting} />
        </li>
      ))}
    </ol>
  )
}

function SpendLimitField({ range, value, disabled, onChange }: { range: readonly number[]; value: number; disabled: boolean; onChange: (v: number) => void }) {
  return (
    <Field label="Spend limit" unit="$" help="The most this session may spend on the paid engine. Once its translations cost this much, the session stops; the lines so far are kept. A monthly cap in Settings replaces this limit.">
      <input type="number" inputMode="decimal" min={range[0]} max={range[1]} step={0.5}
        data-testid="live-spend-limit" value={value} disabled={disabled} onChange={(e) => onChange(e.target.valueAsNumber)} />
    </Field>
  )
}

export default function LivePage() {
  const pc = usePcOnly()
  // What Start accepts; the built-in copy until the server answers (Start checks again anyway).
  const [opts, setOpts] = useState(LIVE_FORM_FALLBACK)
  const { prefs, setPrefs, setOpt, changeLanguage } = useLivePrefs(opts)
  useEffect(() => {
    let alive = true
    getLiveOptions().then((o) => { if (alive) setOpts(o) }, () => {})
    return () => { alive = false }
  }, [])
  const [fastHelpId, balancedHelpId] = [useId(), useId()]
  const [url, setUrl] = useState('')
  const [showVideo, setShowVideo] = usePersistedState<boolean>('live.showVideo', true)
  const [captionsOn, setCaptionsOn] = usePersistedState<boolean>('live.captions', true)
  const [theater, setTheater] = usePersistedState<boolean>('live.theater', false)
  const [videoDelay, setVideoDelay] = usePersistedState<number>('live.videoDelay', DEFAULT_DELAY)
  const [delayAuto, setDelayAuto] = usePersistedState<boolean>('live.videoDelayAuto', true)
  // The delayed player records on the PC and the server refuses it from away, so a choice
  // remembered on this browser is set aside there (the PC keeps it).
  const language = prefs.source_language ?? opts.defaults.source_language
  const presetModel = prefs.caption_preset && prefs.whisper_size === undefined
    ? captionPresetModel(prefs.caption_preset, opts, language) : undefined
  const form: LiveForm = { ...formDefaults(opts, prefs.source_language), ...prefs, ...(presetModel ? { whisper_size: presetModel } : {}), url, ...(pc === 'remote' ? { delayed_player: false } : {}) }
  const holdsImport = useHolds('media.import_url')

  // Titles and series the server lets this user see; null until read. A failed read leaves the picker empty.
  const [titles, setTitles] = useState<{ id: number; name: string; series_id: number | null }[] | null>(null)
  const [seriesList, setSeriesList] = useState<{ id: number; name: string }[] | null>(null)
  const [engines, setEngines] = useState<TranslateEngine[] | null>(null)
  const [enginesFailed, setEnginesFailed] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const { session, sessionId, stopping, began, stop, adoptRunning } = useLiveSession(setError)

  useEffect(() => {
    translateApi.engines().then(setEngines, () => {
      setEngines([])
      setEnginesFailed(true)
    })
    api.listDramas({ progress: 'false' }).then(
      (r) => setTitles(r.items.map((d) => ({ id: d.id, name: d.title_en || d.title_zh || `#${d.id}`, series_id: d.series_id ?? null }))),
      () => setTitles([]),
    )
    getAllSeries().then(
      (r) => setSeriesList(r.items.map((s) => ({ id: s.id, name: s.name }))),
      () => setSeriesList([]),
    )
  }, [])

  // A remembered title or series that was deleted or hidden is not sent.
  const glossaryOptions = glossaryChoices(seriesList ?? [], titles ?? [])
  const glossaryPick = glossaryValue(form, glossaryOptions)
  const glossaryGroups = [...new Set(glossaryOptions.map((o) => o.group))]
  const glossary = glossaryIds(glossaryPick)

  const usable = engines ? usableEngines(engines) : []
  const engine = pickEngine(usable, form.engine, opts.default_engine)

  const canSwitchThinking = opts.thinking_switch_engines.includes(engine)
  const { entry: engineEntry, onlyCloud, defaultModel, model, fellBack } = resolveLiveModel(usable.find((e) => e.name === engine), form.model, opts)
  const modelNote = enginesFailed
    ? "Couldn't load the model list; the engine's default model will be used."
    : fellBack ? "That model isn't offered any more; the engine's default model will be used." : null

  const { readiness } = useReadiness()
  const gpuReason = liveGpuReason(readiness)
  // GPU is on unless this browser unticked it: a CPU Whisper can't keep up with a stream, so
  // an untouched form must not start there. With no NVIDIA GPU the box shows unticked, so a
  // remembered tick is not sent either.
  const useGpu = (prefs.use_gpu ?? readiness?.gpu_found === true) && !gpuReason
  const startForm: LiveForm = { ...form, engine, model, ...glossary, use_gpu: useGpu }
  // Ollama is the default and runs on this PC: say plainly when it is not
  // there, instead of failing at Start. Nothing switches engine for the user.
  const ollama = useOllamaCheck(engine === 'ollama' ? model : null, useGpu ? form.whisper_size : null)
  // Until Start is pressed a missing Ollama is only information; it becomes an alert when it blocks a start.
  const [startPressed, setStartPressed] = useState(false)
  const developerMode = useDeveloperMode()

  const status = session?.status?.status ?? (session ? 'queued' : null)
  const active = !!session && isActive(status)
  const urlReason = checkLiveUrl(url, opts.max_url_len)
  // While the engine list loads there is no engine to send; never start then
  // (a missing engine would fall back to the server default, a paid one).
  const needs = liveNeeds(readiness, { delayedPlayer: form.delayed_player })
  const needList = [...liveBlockers(needs), ...(needs.jsRuntime ? [needs.jsRuntime] : [])]
  const sizeReason = liveWhisperSize(readiness, form.whisper_size, pc !== 'remote').reason
  const blocker = urlReason ?? (engines === null
    ? 'Loading engines…'
    : !engine ? 'Still needed: an engine with a key (see Settings).'
      : liveBlockers(needs).length ? 'Still needed: what is listed under Start.'
        : sizeReason ? `Still needed: a Whisper model that is ready. ${sizeReason}` : null)

  async function start(e?: React.FormEvent, anyway = false) {
    e?.preventDefault()
    setStartPressed(true)
    if (blocker || busy || active) return
    if (anyway) ollama.accept()
    else if (ollama.holdStart()) return
    setBusy(true)
    setError(null)
    try {
      const body = buildStartBody(startForm, opts)
      const { session_id } = await startLive(body)
      began(session_id, body.url)
    } catch (err) {
      setError(describeLiveError(err))
      if (hasStatus(err, 409)) adoptRunning(describeLiveError(err))
    } finally {
      setBusy(false)
    }
  }

  // The picture exists only while the session runs and Stop has not been
  // pressed: leaving it mounted would keep the stream's audio playing.
  // What the running session records, not the form. Its files are served to the PC only.
  // A player that stopped with a stream break and could not start again gives way to the site's embed.
  const ownPlayer = session?.status?.delayed_player === true && session.status.player_stopped !== true && pc !== 'remote'
  const videoLive = !!session && status === 'running' && !stopping && (!!session.url || !!session.status?.stream || ownPlayer)
  const streamRef = videoLive ? sessionStream(session?.url, session?.status?.stream) : null
  const feed = session ? feedCues(session.cues) : []
  const total = session?.next ?? 0
  const lag = session?.status ? lagLine(session.status) : null

  // Held steady between small changes of the estimate: each change seeks the picture.
  const estimate = autoDelay(session?.status?.segment_seconds || form.segment_seconds,
    session?.status?.processing_seconds ?? 0, (session?.status?.chunks_measured ?? 0) > 0)
  const [autoValue, setAutoValue] = useState<number | null>(null)
  // A new session's estimate starts from its own chunks, not the last session's.
  const [valueFor, setValueFor] = useState(sessionId)
  if (valueFor !== sessionId) {
    setValueFor(sessionId)
    setAutoValue(null)
  }
  const steadyValue = steadyDelay(valueFor === sessionId ? autoValue : null, estimate)
  if (steadyValue !== autoValue) setAutoValue(steadyValue)
  // What the player reports it really runs behind live; the chat panel waits that long.
  const [pictureDelay, setPictureDelay] = useState(0)
  const delay = delayAuto ? (autoValue ?? estimate) : videoDelay

  return (
    <PageShell width="standard" title="Live" label="Live" className={videoLive ? 'live-page live-wide' : 'live-page'}>
      <div className="panel">
        {/* noValidate: buildStartBody clamps the numbers, as the service does. */}
        <form onSubmit={start} className="live-form" noValidate>
          <Field label="Stream link" help="A live stream or video page (YouTube, Bilibili, …) that yt-dlp can open. Signed-in (cookie) streams don't work from here.">
            <input
              type="url"
              inputMode="url"
              autoComplete="off"
              spellCheck={false}
              maxLength={opts.max_url_len}
              placeholder="https://www.youtube.com/watch?v=…"
              value={url}
              disabled={active || busy}
              onChange={(e) => setUrl(e.target.value)}
            />
          </Field>
          <div className="field-row">
            <Field label="Language">
              <select value={form.source_language} disabled={active || busy} onChange={(e) => changeLanguage(e.target.value)}>
                {opts.languages.map((l) => (
                  <option key={l.code} value={l.code}>
                    {l.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={AI_ENGINE_LABEL} help="Engines without a key are hidden; add keys in Settings.">
              <select value={engine} disabled={active || busy || !usable.length} onChange={(e) => setOpt('engine', e.target.value)}>
                {!usable.length && <option value="">{engines ? '' : 'Loading…'}</option>}
                {usable.map((en) => (
                  <option key={en.name} value={en.name}>
                    {engineShortName(en)}
                    {en.free ? '' : ' (paid)'}
                  </option>
                ))}
              </select>
            </Field>
            {engineEntry && !engineEntry.free && !opts.monthly_cap_set && (
              <SpendLimitField range={opts.spend_limit_range} value={form.spend_limit_usd} disabled={active || busy} onChange={(v) => setOpt('spend_limit_usd', v)} />
            )}
            <ModelSelect engine={engineEntry} value={model} disabled={active || busy} onChange={(m) => setOpt('model', m)}
              defaultModel={engine === opts.default_engine ? defaultModel ?? undefined : undefined}
              help={engine === opts.default_engine
                ? (onlyCloud
                  ? "Runs on Ollama's servers, so the text leaves this PC and the graphics card stays free for Whisper. Pick another engine above for a different service."
                  : 'Runs on this PC through Ollama. Pick another engine above to send the text to a hosted service instead.')
                : 'Engine default uses the model the engine runs on its own.'} />
          </div>
          {engines && !usable.length && (
            <p className="muted" data-testid="live-no-engine-note">
              <a href={routeHref({ name: 'settings' })}>Add a key in Settings</a>
            </p>
          )}
          {engines && !usable.length && developerMode && (
            <p className="muted" data-testid="live-echo-note">Echo (testing) is available in Developer Mode</p>
          )}
          <Field label="Use glossary and packs from"
            help="Optional. The glossary always comes from a series. Pick a series (uses the packs of its newest title that has a pack choice) or a title (uses its series' glossary and its own packs). Terms found in the stream are sent with each translation. Nothing is written to the library.">
            <select value={glossaryPick} disabled={active || busy} data-testid="live-glossary-title"
              onChange={(e) => setPrefs({ ...prefs, ...glossaryIds(e.target.value) })}>
              <option value="">None</option>
              {glossaryGroups.map((g) => (
                <optgroup key={g} label={g}>
                  {glossaryOptions.filter((o) => o.group === g).map((o) => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </optgroup>
              ))}
            </select>
          </Field>
          {ollama.note && <OllamaNote check={ollama} alert={startPressed && !ollama.fitWarning} onStartAnyway={() => void start(undefined, true)} />}
          {modelNote && <p className="muted" data-testid="live-model-note">{modelNote}</p>}
          <Section storageKey="live.advanced" title="Advanced (defaults are fine)" summary={advancedSummary(startForm, opts)}>
            <div className="preset-note-row">
              <button type="button" className="secondary" data-testid="live-fast-captions" disabled={active || busy}
                onClick={() => setPrefs(applyCaptionPreset(prefs, 'fast'))}>
                Fast captions
              </button>
              <span className="muted">Replaces Whisper model, Chunk, Overlap and Reply without thinking.</span>
              <HelpTip label="Fast captions" id={fastHelpId}>
                {`Chunk ${FAST_CAPTIONS.segment_seconds} s, overlap ${FAST_CAPTIONS.overlap_seconds} s, Whisper ${FAST_CAPTIONS.whisper_size} (or, for the language picked, the model this PC has downloaded: ${LANGUAGE_MODEL_NOTE}), ${FAST_CAPTIONS.reply_without_thinking ? 'no thinking' : 'thinking allowed'}: lines show sooner, but Whisper hears less context, so wording is rougher. It replaces your current values for these four options, and this browser remembers them; to go back, set them by hand (the app's defaults are chunk ${opts.defaults.segment_seconds} s, overlap ${opts.defaults.overlap_seconds} s, Whisper ${opts.defaults.whisper_size}, ${DEFAULT_FORM.reply_without_thinking ? 'no thinking' : 'thinking allowed'}).`}
              </HelpTip>
            </div>
            <div className="preset-note-row">
              <button type="button" className="secondary" data-testid="live-balanced-captions" disabled={active || busy}
                onClick={() => setPrefs(applyCaptionPreset(prefs, 'balanced'))}>
                Balanced captions
              </button>
              <span className="muted">Replaces Whisper model, Chunk, Overlap and Reply without thinking.</span>
              <HelpTip label="Balanced captions" id={balancedHelpId}>
                {`Chunk ${BALANCED_CAPTIONS.segment_seconds} s, overlap ${BALANCED_CAPTIONS.overlap_seconds} s, Whisper ${BALANCED_CAPTIONS.whisper_size} (or, for the language picked, the model this PC has downloaded: ${LANGUAGE_MODEL_NOTE}), no thinking: lines show about 10 s after they are spoken with far fewer mistakes than Fast captions. Needs a GPU for Whisper large-v3 (about 3 GB download the first time). It replaces your current values for these four options, and this browser remembers them; to go back, set them by hand.`}
              </HelpTip>
            </div>
            <div className="field-row">
              <Field label="Whisper model" help={WHISPER_HELP}>
                <select value={form.whisper_size} disabled={active || busy} onChange={(e) => setOpt('whisper_size', e.target.value)}>
                  {opts.whisper_sizes.map((w) => {
                    const size = liveWhisperSize(readiness, w, pc !== 'remote')
                    const wrongLanguage = !!whisperExclusionReason(opts.whisper_exclusions, w, form.source_language)
                    return (
                      <option key={w} value={w} disabled={wrongLanguage || (!!size.reason && w !== form.whisper_size)}>
                        {w}{liveWhisperOptionNote(wrongLanguage, size)}
                      </option>
                    )
                  })}
                </select>
              </Field>
              <Field label="Chunk" unit="s" help="Shorter shows lines sooner; longer transcribes better per chunk.">
                <input type="number" min={opts.segment_range[0]} max={opts.segment_range[1]} step={1} value={numValue(form.segment_seconds)} disabled={active || busy}
                  onChange={(e) => setOpt('segment_seconds', e.target.valueAsNumber)} />
              </Field>
              <Field label="Overlap" unit="s" help="Re-hears this much of the previous chunk so a sentence cut at a boundary is heard whole. 0 turns it off; at most half the chunk.">
                <input type="number" min={opts.overlap_range[0]} max={opts.overlap_range[1]} step={1} value={numValue(form.overlap_seconds)} disabled={active || busy}
                  onChange={(e) => setOpt('overlap_seconds', e.target.valueAsNumber)} />
              </Field>
              <Field label="Stop after" unit="min" help="A hard cap on how long the session runs (and spends). Leave it blank to caption until the stream ends; the spend limit still applies.">
                <input type="number" min={opts.max_minutes_range[0]} max={opts.max_minutes_range[1]} step={1} value={numValue(form.max_minutes)} disabled={active || busy}
                  placeholder="no limit" onChange={(e) => setOpt('max_minutes', Number.isFinite(e.target.valueAsNumber) ? e.target.valueAsNumber : null)} />
              </Field>
            </div>
            {opts.whisper_sizes.map((w) => {
              const reason = whisperExclusionReason(opts.whisper_exclusions, w, form.source_language) ?? liveWhisperSize(readiness, w, pc !== 'remote').reason
              return reason ? <p key={w} className="muted" data-testid="live-whisper-reason">{w}: {reason}</p> : null
            })}
            <label className="live-check">
              <input type="checkbox" checked={useGpu} disabled={active || busy || !!gpuReason} onChange={(e) => setOpt('use_gpu', e.target.checked)} />
              Use GPU for Whisper
            </label>
            {gpuReason && <p className="muted" data-testid="live-gpu-note">{gpuReason}</p>}
            {pc !== 'remote' && (
              <Field label="Delayed player"
                help="Records a delayed copy of the video on this PC and plays it instead of the stream's player, so the picture matches the lines even where the stream can't be rewound. Downloads video as well as audio. Takes effect on Start.">
                <Toggle checked={form.delayed_player} disabled={active || busy} onChange={(v) => setOpt('delayed_player', v)} />
              </Field>
            )}
            {form.delayed_player && (
              <Field label="Delayed player quality" help="Tallest picture recorded for the delayed player. Taller is sharper but downloads and stores more (1080p is about 5 Mbit/s). A stream without it uses the nearest smaller size. Takes effect on Start.">
                <select value={form.player_height} disabled={active || busy} onChange={(e) => setOpt('player_height', Number(e.target.value))}>
                  {opts.player_heights.map((h) => <option key={h} value={h}>{h}p</option>)}
                </select>
              </Field>
            )}
            <ReadChatField checked={form.read_chat} disabled={active || busy} onChange={(v) => setOpt('read_chat', v)} />
            <Field label="Reply without thinking"
              help={`Faster lines: the translator answers straight away instead of reasoning first. Works with ${listNames(opts.thinking_switch_engines)}.`}>
              <Toggle checked={form.reply_without_thinking && canSwitchThinking}
                disabled={active || busy || !canSwitchThinking}
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
              <button type="button" className={buttonClass('secondary')} onClick={() => void stop()} disabled={stopping === 'now'} aria-busy={stopping === 'now' || undefined}>
                {stopping === 'finishing' ? 'Stop now' : stopping ? 'Stopping…' : status === 'queued' ? 'Cancel' : 'Stop'}
              </button>
            ) : (
              <button type="submit" className="primary" disabled={!!blocker || busy}>
                {busy ? 'Starting…' : 'Start'}
              </button>
            )}
          </div>
          {!active && blocker && <p className="muted"><SettingsMentions text={blocker} /></p>}
          {!active && needList.length > 0 && <div className="stack">{needList.map((n) => <NeedsInstall key={n.id} need={n} />)}</div>}
          {pc === 'remote' && !active && !holdsImport && (
            <p className="muted">From this device, starting needs the owner to allow importing from links.</p>
          )}
        </form>
        {error && (
          <p className="error live-error" role="alert">
            {error}
          </p>
        )}
        <FollowedChannels liveOptions={followOptions(buildStartBody(startForm, opts))} formOptions={opts} engines={engines && usable.map((e) => liveEngineView(e, cloudAllowed) ?? e)} gpuReason={gpuReason} />

        {session && (
          <div className={videoLive ? (theater ? 'live-body has-video theater' : 'live-body has-video') : 'live-body'}>
          {videoLive && (
            <div className="card live-video" role="group" aria-label="Stream video">
              <div className="card-head">
                <h2 className="card-title">Video</h2>
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
              {!streamRef && !ownPlayer && <p className="muted">This site can't be shown here; the lines still work.</p>}
              {(streamRef || ownPlayer) && showVideo && (
                <>
                  {ownPlayer ? (
                    <DelayedPlayer sessionId={session.id} ready={session.status?.player_ready === true} delay={delay} onPictureDelay={setPictureDelay}
                      captions={captionsOn ? (d) => <LiveCaptions key={session.id} cues={session.cues} delay={d} chunkSeconds={session.status?.segment_seconds} /> : undefined} />
                  ) : streamRef && (
                    <StreamEmbed stream={streamRef} delay={delay} onPictureDelay={setPictureDelay}
                      captions={captionsOn ? (d) => <LiveCaptions key={session.id} cues={session.cues} delay={d} chunkSeconds={session.status?.segment_seconds} /> : undefined} />
                  )}
                  {(ownPlayer || (streamRef && canDelay(streamRef))) && (
                    <>
                      <Field label="Video delay" unit="s" help="The translation arrives several seconds after the speech. The picture plays this far behind live so they line up. Moving the slider sets it by hand.">
                        <input type="range" min={DELAY_RANGE[0]} max={DELAY_RANGE[1]} step={1} value={delay} aria-valuetext={`${delay} s`}
                          onChange={(e) => {
                            setDelayAuto(false)
                            setVideoDelay(e.target.valueAsNumber)
                          }} />
                      </Field>
                      <Field label="Automatic video delay" help="Sets the delay from the chunk length and how long the last chunks took to caption.">
                        <Toggle checked={delayAuto} onChange={setDelayAuto} />
                      </Field>
                      <p className="muted" data-testid="live-delay-mode">
                        {delayAuto ? `Video delay: auto (about ${delay} s)` : `Video delay: ${videoDelay} s, set by hand`}
                      </p>
                    </>
                  )}
                </>
              )}
            </div>
          )}
          <div className="live-feed">
            {/* Only the status line is announced: the lines below change every chunk. */}
            <p className={session.status ? statusTone(session.status) : 'muted'} data-testid="live-status" role="status">
              {session.status ? statusLine(session.status, total) : 'Connecting…'}
            </p>
            {lag && <p className="muted" data-testid="live-lag">{lag}</p>}
            {!!session.status?.notes?.length && (
              <ul className="muted live-notes" data-testid="live-notes" aria-label="Live events">
                {session.status.notes.map((n, i) => <li key={`${i}-${n}`}>{n}</li>)}
              </ul>
            )}
            {active && status === 'running' && !feed.length && <p className="muted">Waiting for the first chunk…</p>}
            {feed.length > 0 && <CueFeed cues={feed} lang={session.status?.source_language ?? form.source_language} waiting={active} />}
            {total > feed.length && <p className="muted">Showing the newest {feed.length} of {total} lines.</p>}
            {total > 0 && <SaveSession cues={session.cues} />}
          </div>
          </div>
        )}
        {session?.status?.chat === true && <LiveChatSection sessionId={session.id} running={active} language={session.status.source_language ?? form.source_language}
          delay={showVideo && (streamRef || ownPlayer) ? pictureDelay : 0} />}
      </div>
    </PageShell>
  )
}
