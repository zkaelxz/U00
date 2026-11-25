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
import { useLiveChoices } from './live/useLiveChoices'
import { cancelsAnyJob, useSession } from '../hooks/useSession'

import { hasStatus } from '../api/client'
import {
  DEFAULT_FORM, LIVE_FORM_FALLBACK, FAST_CAPTIONS, BALANCED_CAPTIONS, WHISPER_HELP, LANGUAGE_MODEL_NOTE,
  advancedSummary, applyCaptionPreset, buildStartBody, captionPresetModel, checkLiveUrl, chosenForYou, describeLiveError, formDefaults, getLiveOptions, glossaryChoices, glossaryIds, glossaryValue,
  isActive, liveEngineView, pickEngine, resolveLiveModel, startLive, type LiveForm,
} from '../api/live'
import { engineShortName, usableEngines } from '../api/translate'
import { ChosenForYouNote } from '../components/ChosenForYou'
import { useChosenForYou } from '../hooks/useChosenForYou'
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
import { followOptions } from '../api/liveFollows'
import { useDeveloperMode } from './assistant/developerMode'
import { NeedsInstall } from './readiness/NeedsInstall'
import { liveBlockers, liveGpuReason, liveNeeds, liveWhisperOptionNote, liveWhisperSize } from './readiness/needs'
import { whisperExclusionReason } from './workspace/sourceForm'
import { FollowedChannels } from './live/FollowedChannels'
import { LiveNowCards } from './live/LiveNowCards'
import { useFollows } from './live/useFollows'
import { ReadChatField } from './live/LiveChat'
import { useLiveSession } from './live/useLiveSession'
import { LiveWatch } from './live/LiveWatch'
import { OllamaNote } from './live/OllamaNote'
import { useOllamaCheck } from './live/useOllamaCheck'
import { parseStreamUrl } from './live/embedLogic'
import './live.css'
import { AI_ENGINE_LABEL } from '../helpText'
import { routeHref, useRoute } from '../router'

const numValue = (n: number | null) => (n !== null && Number.isFinite(n) ? n : '')

/** "A and B", "A, B and C": engine names as the picker shows them. */
const listNames = (names: string[]) => {
  const shown = names.map((name) => engineShortName({ name }))
  return shown.length > 1 ? `${shown.slice(0, -1).join(', ')} and ${shown[shown.length - 1]}` : shown.join('')
}


/** An admin's (or the PC owner's) switch to list every account's sessions, not just their own. */
function ShowEveryone({ onChange }: { onChange: (on: boolean) => void }) {
  const [on, setOn] = useState(false)
  if (!cancelsAnyJob(useSession())) return null
  return (
    <label className="checkbox-row">
      <input type="checkbox" checked={on} onChange={(e) => { setOn(e.target.checked); onChange(e.target.checked) }} />
      Show everyone's sessions
    </label>
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

export default function LivePage({ initialUrl = '' }: { initialUrl?: string }) {
  const pc = usePcOnly()
  // What Start accepts; the built-in copy until the server answers (Start checks again anyway).
  const [opts, setOpts] = useState(LIVE_FORM_FALLBACK)
  const { prefs: savedPrefs, setPrefs, setOpt, changeLanguage } = useLivePrefs(opts)
  // Only the stream link, language and glossary are shown; Start sends the server's defaults for the rest.
  const chosen = useChosenForYou()
  const auto = chosen.auto
  const prefs = auto ? chosenForYou(savedPrefs) : savedPrefs
  useEffect(() => {
    let alive = true
    getLiveOptions().then((o) => { if (alive) setOpts(o) }, () => {})
    return () => { alive = false }
  }, [])
  const [fastHelpId, balancedHelpId] = [useId(), useId()]
  const [url, setUrl] = useState(initialUrl)
  // The delayed player records on the PC and the server refuses it from away, so a choice
  // remembered on this browser is set aside there (the PC keeps it).
  const language = prefs.source_language ?? opts.defaults.source_language
  const presetModel = prefs.caption_preset && prefs.whisper_size === undefined
    ? captionPresetModel(prefs.caption_preset, opts, language) : undefined
  const form: LiveForm = { ...formDefaults(opts, prefs.source_language), ...prefs, ...(presetModel ? { whisper_size: presetModel } : {}), url, ...(pc === 'remote' ? { delayed_player: false } : {}) }
  const holdsImport = useHolds('media.import_url')
  const follows = useFollows()
  const route = useRoute()
  const pickedFollow = route.name === 'live' && route.follow ? route.follow : null

  const { titles, seriesList, engines, enginesFailed } = useLiveChoices()
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const { session, stopping, joined, began, stop, adoptRunning, showEveryone, clearLines, switchTo } = useLiveSession(setError)

  // A remembered title or series that was deleted or hidden is not sent.
  const glossaryOptions = glossaryChoices(seriesList ?? [], titles ?? [])
  const glossaryPick = glossaryValue(form, glossaryOptions)
  const glossaryGroups = [...new Set(glossaryOptions.map((o) => o.group))]
  const glossary = glossaryIds(glossaryPick)

  const usable = engines ? usableEngines(engines) : []
  // Chosen for the viewer: Live's own default (free for every account), even when this browser's engine list
  // lacks it, so Start never falls back to a paid engine they may not use; the server says if it isn't ready.
  const engine = auto ? opts.default_engine : pickEngine(usable, form.engine, opts.default_engine)

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
  // Twitch can't be held back, so its delayed-player option isn't offered.
  const twitchLink = !!parseStreamUrl(url)?.kind.startsWith('twitch')
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
      const { session_id, joined: joinedRunning } = await startLive(body)
      began(session_id, body.url, joinedRunning)
    } catch (err) {
      setError(describeLiveError(err))
      if (hasStatus(err, 409)) adoptRunning(describeLiveError(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <PageShell width="standard" title="Live" label="Live" className={active ? 'live-page live-wide' : 'live-page'}>
      <div className="panel">
        {!active && <LiveNowCards items={follows.list?.items ?? []} picked={pickedFollow} />}
        {/* noValidate: buildStartBody clamps the numbers, as the service does. */}
        <form onSubmit={start} className={active ? 'live-form watching' : 'live-form'} noValidate>
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
            {!auto && <Field label={AI_ENGINE_LABEL} help="Engines without a key are hidden; add keys in Settings.">
              <select value={engine} disabled={active || busy || !usable.length} onChange={(e) => setOpt('engine', e.target.value)}>
                {!usable.length && <option value="">{engines ? '' : 'Loading…'}</option>}
                {usable.map((en) => (
                  <option key={en.name} value={en.name}>
                    {engineShortName(en)}
                    {en.free ? '' : ' (paid)'}
                  </option>
                ))}
              </select>
            </Field>}
            {!auto && engineEntry && !engineEntry.free && !opts.monthly_cap_set && (
              <SpendLimitField range={opts.spend_limit_range} value={form.spend_limit_usd} disabled={active || busy} onChange={(v) => setOpt('spend_limit_usd', v)} />
            )}
            {!auto && <ModelSelect engine={engineEntry} value={model} disabled={active || busy} onChange={(m) => setOpt('model', m)}
              defaultModel={engine === opts.default_engine ? defaultModel ?? undefined : undefined}
              help={engine === opts.default_engine
                ? (onlyCloud
                  ? "Runs on Ollama's servers, so the text leaves this PC and the graphics card stays free for Whisper. Pick another engine above for a different service."
                  : 'Runs on this PC through Ollama. Pick another engine above to send the text to a hosted service instead.')
                : 'Engine default uses the model the engine runs on its own.'} />}
          </div>
          {auto && <ChosenForYouNote text="The engine and caption settings are chosen for you." chosen={chosen} testId="live-chosen-note" />}
          {!auto && engines && !usable.length && (
            <p className="muted" data-testid="live-no-engine-note">
              <a href={routeHref({ name: 'settings' })}>Add a key in Settings</a>
            </p>
          )}
          {!auto && engines && !usable.length && developerMode && (
            <p className="muted" data-testid="live-echo-note">Echo (testing) is available in Developer Mode</p>
          )}
          <Field label="Use glossary and packs from"
            help="Optional. The glossary always comes from a series. Pick a series (uses the packs of its newest title that has a pack choice) or a title (uses its series' glossary and its own packs). Terms found in the stream are sent with each translation. Nothing is written to the library.">
            <select value={glossaryPick} disabled={active || busy} data-testid="live-glossary-title"
              onChange={(e) => setPrefs({ ...savedPrefs, ...glossaryIds(e.target.value) })}>
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
          {!auto && <Section storageKey="live.advanced" title="Advanced (defaults are fine)" summary={advancedSummary(startForm, opts)}>
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
            {pc !== 'remote' && !twitchLink && (
              <Field label="Delayed player"
                help="Records a delayed copy of the video on this PC and plays it instead of the stream's player, so the picture matches the lines even where the stream can't be rewound. Downloads video as well as audio. Takes effect on Start.">
                <Toggle checked={form.delayed_player} disabled={active || busy} onChange={(v) => setOpt('delayed_player', v)} />
              </Field>
            )}
            {form.delayed_player && !twitchLink && (
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
          </Section>}
          {!active && (
            <div className="actions">
              <button type="submit" className="primary" disabled={!!blocker || busy}>
                {busy ? 'Starting…' : 'Start'}
              </button>
            </div>
          )}
          {!active && blocker && <p className="muted"><SettingsMentions text={blocker} /></p>}
          {!active && needList.length > 0 && <div className="stack">{needList.map((n) => <NeedsInstall key={n.id} need={n} />)}</div>}
          {pc === 'remote' && !active && !holdsImport && (
            <p className="muted">From this device, starting needs the owner to allow importing from links.</p>
          )}
        </form>
        <ShowEveryone onChange={showEveryone} />
        {error && (
          <p className="error live-error" role="alert">
            {error}
          </p>
        )}
        {!active && <FollowedChannels follows={follows} picked={pickedFollow} liveOptions={followOptions(buildStartBody(startForm, opts))} formOptions={opts} engines={engines && usable.map((e) => liveEngineView(e) ?? e)} gpuReason={gpuReason} />}

        {session && (
          <LiveWatch session={session} stopping={stopping} joined={joined} onStop={() => void stop()} onClear={clearLines}
            onSwitch={switchTo} language={form.source_language} segmentSeconds={form.segment_seconds} />
        )}
      </div>
    </PageShell>
  )
}
